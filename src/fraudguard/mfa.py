"""RFC 6238 authenticator codes, encrypted secrets and one-use recovery codes."""

import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException


def cipher():
    try:
        return Fernet(os.environ["MFA_ENCRYPTION_KEY"].encode())
    except (KeyError, ValueError):
        raise HTTPException(503, "Authenticator encryption is not configured") from None


def required():
    return os.getenv("REQUIRE_MFA", "false").lower() == "true"


def totp(secret, counter, digits=6):
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def matched(secret, code, last=-1):
    if not re.fullmatch(r"[0-9]{6}", code):
        return None
    current = int(time.time() // 30)
    for step in (current, current - 1, current + 1):
        if step > last and hmac.compare_digest(totp(secret, step), code):
            return step
    return None


def challenge(store, username):
    cipher()  # Fail closed before issuing an unusable enrollment token.
    token = secrets.token_urlsafe(32)
    with store.connect() as db:
        db.execute("DELETE FROM enrollments WHERE username=? OR expires<?", (username, time.time()))
        db.execute(
            "INSERT INTO enrollments VALUES(?,?,?,NULL,0)",
            (hashlib.sha256(token.encode()).hexdigest(), username, time.time() + 600),
        )
    return {"enrollment_required": True, "enrollment_token": token, "expires_in": 600}


def enrollment(db, token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute(
        "SELECT e.* FROM enrollments e JOIN users u ON u.name=e.username WHERE e.token=? AND e.expires>? AND e.attempts<5 AND u.active=1",
        (digest, time.time()),
    ).fetchone()
    if not row:
        raise HTTPException(401, "Enrollment expired. Sign in again.")
    if db.execute("SELECT 1 FROM mfa WHERE username=?", (row["username"],)).fetchone():
        raise HTTPException(409, "Authenticator already enabled")
    return row


def setup(store, token):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = enrollment(db, token)
        encrypted = row["secret"]
        if not encrypted:
            secret = base64.b32encode(secrets.token_bytes(20)).decode()
            encrypted = cipher().encrypt(secret.encode()).decode()
            db.execute("UPDATE enrollments SET secret=? WHERE token=?", (encrypted, row["token"]))
        else:
            secret = cipher().decrypt(encrypted.encode()).decode()
    uri = (
        "otpauth://totp/"
        + quote("FraudGuard:" + row["username"], safe="")
        + "?"
        + urlencode(
            {
                "secret": secret,
                "issuer": "FraudGuard",
                "algorithm": "SHA1",
                "digits": 6,
                "period": 30,
            }
        )
    )
    return {"secret": secret, "provisioning_uri": uri, "account": row["username"]}


def confirm(store, token, code):
    codes = None
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = enrollment(db, token)
        db.execute("UPDATE enrollments SET attempts=attempts+1 WHERE token=?", (row["token"],))
        secret = cipher().decrypt(row["secret"].encode()).decode() if row["secret"] else ""
        step = matched(secret, code) if secret else None
        if step is not None:
            codes = [secrets.token_hex(10) for _ in range(8)]
            db.execute("INSERT INTO mfa VALUES(?,?,?)", (row["username"], row["secret"], step))
            db.executemany(
                "INSERT INTO recovery_codes VALUES(?,?)",
                [(row["username"], hashlib.sha256(c.encode()).hexdigest()) for c in codes],
            )
            db.execute("DELETE FROM enrollments WHERE username=?", (row["username"],))
            db.execute("DELETE FROM sessions WHERE username=?", (row["username"],))
            db.execute("DELETE FROM attempts WHERE bucket=?", ("user:" + row["username"].lower(),))
            store.audit(db, row["username"], "mfa.enabled")
    if codes is None:
        raise HTTPException(400, "Invalid authenticator code. Check your phone clock.")
    return {
        "recovery_codes": codes,
        "message": "Save these one-use codes privately. Sign in with the next authenticator code.",
    }


def verify(db, username, code):
    row = db.execute("SELECT * FROM mfa WHERE username=?", (username,)).fetchone()
    if not row:
        return False
    if re.fullmatch(r"[a-f0-9]{20}", code):
        return (
            db.execute(
                "DELETE FROM recovery_codes WHERE username=? AND digest=?",
                (username, hashlib.sha256(code.encode()).hexdigest()),
            ).rowcount
            == 1
        )
    try:
        secret = cipher().decrypt(row["secret"].encode()).decode()
    except InvalidToken:
        raise HTTPException(
            503, "Authenticator secret cannot be decrypted; contact the operator"
        ) from None
    step = matched(secret, code, row["last_step"])
    if step is None:
        return False
    db.execute("UPDATE mfa SET last_step=? WHERE username=?", (step, username))
    return True

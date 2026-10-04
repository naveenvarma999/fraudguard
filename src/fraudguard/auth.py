"""Password hashing and expiring, revocable bearer sessions."""

import hashlib
import math
import secrets
import threading
import time

from fastapi import HTTPException

from fraudguard import mfa

HASH_SLOTS = threading.BoundedSemaphore(2)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    if not HASH_SLOTS.acquire(timeout=1):
        raise HTTPException(429, "Password verification is busy; retry shortly")
    try:
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=67108864
        ).hex()
    finally:
        HASH_SLOTS.release()
    return f"{salt}:{digest}"


DUMMY = password_hash("not-a-real-user-password", "0" * 32)


def add_user(store, username, password, role, actor="operator"):
    import re

    if (
        not re.fullmatch(r"[a-zA-Z0-9_-]{3,40}", username)
        or not 12 <= len(password) <= 128
        or role not in ("admin", "analyst")
    ):
        raise ValueError(
            "Use a 3–40 character username, 12–128 character password and admin/analyst role"
        )
    encoded = password_hash(password)
    with store.connect() as db:
        db.execute(
            "INSERT INTO users(name,password,role,created) VALUES(?,?,?,?)",
            (username, encoded, role, time.time()),
        )
        store.audit(db, actor, "user.created", username, {"role": role})


def login(store, username, password, address, otp=""):
    now = time.time()
    # request.client is resolved only by the explicitly trusted Uvicorn proxy.
    pair = "login-pair:" + hashlib.sha256(f"{username.lower()}\0{address}".encode()).hexdigest()
    source = "login-source:" + address
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM attempts WHERE started<?", (now - 600,))
        previous = db.execute(
            "SELECT count,started FROM attempts WHERE bucket=?", (pair,)
        ).fetchone()
        if previous and previous["count"] >= 5:
            delay = min(30, 2 ** min(previous["count"] - 5, 5))
            wait = math.ceil(previous["started"] + delay - now)
            if wait > 0:
                raise HTTPException(
                    429,
                    "Login retry delayed for this account and source.",
                    headers={"Retry-After": str(wait)},
                )
        ip = db.execute("SELECT count,started FROM attempts WHERE bucket=?", (source,)).fetchone()
        if ip and ip["started"] > now - 60 and ip["count"] >= 30:
            raise HTTPException(
                429,
                "Login rate limit for this source.",
                headers={"Retry-After": str(max(1, math.ceil(ip["started"] + 60 - now)))},
            )
        if ip and ip["started"] <= now - 60:
            db.execute("DELETE FROM attempts WHERE bucket=?", (source,))
        # Reserve before hashing so concurrent requests cannot bypass the limit.
        db.execute(
            "INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1,started=excluded.started",
            (pair, now),
        )
        db.execute(
            "INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1",
            (source, now),
        )
    users = store.query("SELECT * FROM users WHERE name=?", (username,))
    user = users[0] if users else None
    stored = user["password"] if user else DUMMY
    valid = secrets.compare_digest(password_hash(password, stored.split(":")[0]), stored)
    if not valid or not user or not user["active"]:
        with store.connect() as db:
            store.audit(db, username, "login.failed")
        raise HTTPException(401, "Invalid username or password")
    configured = bool(store.query("SELECT 1 FROM mfa WHERE username=?", (username,)))
    if not configured and mfa.required():
        with store.connect() as db:
            db.execute("DELETE FROM attempts WHERE bucket=?", (pair,))
        return mfa.challenge(store, username)
    if configured:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            verified = mfa.verify(db, username, otp)
            if not verified:
                store.audit(db, username, "mfa.failed")
        if not verified:
            raise HTTPException(401, "Invalid or previously used authenticator/recovery code")
    with store.connect() as db:
        db.execute("DELETE FROM attempts WHERE bucket=?", (pair,))
    return issue_session(store, username, user["role"], now)


def issue_session(store, username, role, now):
    token = secrets.token_urlsafe(32)
    expires = now + 8 * 3600
    with store.connect() as db:
        db.execute("DELETE FROM attempts WHERE bucket=?", ("user:" + username.lower(),))
        db.execute("DELETE FROM sessions WHERE expires<?", (now,))
        db.execute(
            "INSERT INTO sessions VALUES(?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), username, expires),
        )
        db.execute(
            "INSERT INTO session_activity VALUES(?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), now, now),
        )
        store.audit(db, username, "login.success")
    return {
        "access_token": token,
        "expires_at": expires,
        "user": {"name": username, "role": role},
    }


def session_user(store, token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with store.connect() as db:
        rows = list(
            db.execute(
                "SELECT u.name,u.role,a.issued_at FROM sessions s JOIN session_activity a ON a.token=s.token JOIN users u ON s.username=u.name WHERE s.token=? AND s.expires>? AND a.last_seen>? AND u.active=1",
                (digest, now, now - 1800),
            )
        )
        if not rows:
            raise HTTPException(401, "Session expired. Sign in again.")
        db.execute("UPDATE session_activity SET last_seen=? WHERE token=?", (now, digest))
    if mfa.required() and not store.query("SELECT 1 FROM mfa WHERE username=?", (rows[0]["name"],)):
        raise HTTPException(401, "Authenticator enrollment required. Sign in again.")
    return {**dict(rows[0]), "token_hash": digest}

"""Durable, at-least-once HTTPS alert delivery. Disabled without operator configuration."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def enqueue(db, name, active, detail, now):
    event_id = uuid.uuid4().hex
    payload = {
        "event_id": event_id,
        "service": "FraudGuard",
        "alert": name,
        "status": "firing" if active else "resolved",
        "detail": detail,
        "timestamp": now,
    }
    db.execute(
        "INSERT INTO deliveries(id,created,payload) VALUES(?,?,?)",
        (event_id, now, json.dumps(payload)),
    )


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        len(url) > 2048
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError(
            "Alert receiver must be an HTTPS URL without embedded credentials or fragment"
        )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_webhook(url, payload, token):
    validate_url(url)
    headers = {"Content-Type": "application/json", "Idempotency-Key": payload["event_id"]}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise ValueError("Receiver rejected delivery")


def deliver(store, url=None, token=None, sender=send_webhook):
    url = url if url is not None else os.getenv("ALERT_WEBHOOK_URL", "")
    if not url:
        return {"enabled": False, "sent": 0}
    validate_url(url)
    token = token if token is not None else os.getenv("ALERT_WEBHOOK_TOKEN", "")
    sent = 0
    now = time.time()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM deliveries WHERE status='pending' AND next_attempt<=? ORDER BY created LIMIT 5",
                (now,),
            )
        ]
        for row in rows:
            # A lease makes a crashed sender retryable; receiver deduplicates event_id.
            db.execute(
                "UPDATE deliveries SET next_attempt=?,attempts=attempts+1 WHERE id=?",
                (now + 120, row["id"]),
            )
    for row in rows:
        attempts = row["attempts"] + 1
        error = None
        try:
            sender(url, json.loads(row["payload"]), token)
        except urllib.error.HTTPError as exc:
            error = f"HTTP {exc.code}"
        except Exception as exc:
            # Never store receiver URLs, credentials or response bodies.
            error = type(exc).__name__
        with store.connect() as db:
            if error:
                db.execute(
                    "UPDATE deliveries SET status=?,last_error=?,next_attempt=? WHERE id=?",
                    (
                        "dead" if attempts >= 8 else "pending",
                        error,
                        time.time() + min(3600, 30 * 2 ** (attempts - 1)),
                        row["id"],
                    ),
                )
                if attempts >= 8:
                    store.audit(
                        db,
                        "monitor",
                        "notification.exhausted",
                        row["id"],
                        {"attempts": attempts, "error": error},
                    )
            else:
                db.execute(
                    "UPDATE deliveries SET status='sent',delivered=?,last_error=NULL WHERE id=?",
                    (time.time(), row["id"]),
                )
                store.audit(db, "monitor", "notification.sent", row["id"])
                sent += 1
    return {"enabled": True, "sent": sent}

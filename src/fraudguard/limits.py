"""Atomic account quotas plus a bounded process-wide admission budget."""

import math
import threading
import time

from fastapi import HTTPException


class Admission:
    def __init__(self, capacity=120, refill=60):
        self.capacity, self.refill = capacity, refill
        self.tokens, self.updated = float(capacity), time.monotonic()
        self.lock = threading.Lock()

    def take(self):
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill)
            self.updated = now
            if self.tokens < 1:
                return False
            self.tokens -= 1
            return True


def quota(store, owner, rows, now=None):
    """Shared by every session for an account; two budgets debit atomically."""
    now = time.time() if now is None else now
    budgets = [("requests", 60, 1, 1), ("transactions", 3000, 50, rows)]
    wait = 0
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        updates = []
        for kind, capacity, refill, cost in budgets:
            key = f"{owner}:{kind}"
            old = db.execute("SELECT tokens,updated FROM quotas WHERE bucket=?", (key,)).fetchone()
            tokens = capacity if not old else min(capacity, old[0] + max(0, now - old[1]) * refill)
            wait = max(wait, math.ceil((cost - tokens) / refill))
            updates.append((key, tokens - cost, now))
        if wait <= 0:
            db.executemany(
                "INSERT INTO quotas VALUES(?,?,?) ON CONFLICT(bucket) DO UPDATE SET tokens=excluded.tokens,updated=excluded.updated",
                updates,
            )
    if wait > 0:
        raise HTTPException(
            429,
            "Prediction rate limit reached. Retry after the indicated delay.",
            headers={"Retry-After": str(wait)},
        )

"""Independent watchdog with verified backups and durable alert delivery."""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from fraudguard.backups import create_backup
from fraudguard.notifications import deliver
from fraudguard.operations import alert_conditions, update_alerts
from fraudguard.store import Store


def maintenance(store):
    conditions = {}
    with store.connect() as db:
        db.execute(
            "INSERT INTO settings VALUES('delivery_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(bool(os.getenv("ALERT_WEBHOOK_URL"))),),
        )
    backup_dir = os.getenv("BACKUP_DIR", "")
    if backup_dir:
        rows = store.query("SELECT value FROM settings WHERE key='backup_latest'")
        latest = json.loads(rows[0]["value"]) if rows else None
        if (
            not latest
            or time.time() - latest["created"] >= 86400
            or not (Path(backup_dir) / latest["file"]).is_file()
        ):
            try:
                latest = create_backup(store, backup_dir)
            except Exception as exc:
                with store.connect() as db:
                    store.audit(db, "backup", "backup.failed", detail={"error": type(exc).__name__})
                conditions["backup_failure"] = (
                    True,
                    "Verified workspace backup failed; inspect backup volume and registry integrity",
                )
        conditions.setdefault("backup_failure", (False, "Latest backup attempt succeeded"))
        conditions["backup_stale"] = (
            not latest or time.time() - latest["created"] > 26 * 3600,
            "No verified backup within 26 hours",
        )
    if os.getenv("ALERT_WEBHOOK_URL"):
        try:
            deliver(store)
            bad = store.query(
                "SELECT count(*) AS n FROM deliveries WHERE status='dead' OR (status='pending' AND created<?)",
                (time.time() - 900,),
            )[0]["n"]
            conditions["notification_failure"] = (
                bad > 0,
                "Alert deliveries exhausted retries or have been pending over 15 minutes",
            )
        except Exception:
            conditions["notification_failure"] = (
                True,
                "External alert configuration or delivery failed",
            )
    return conditions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    store = Store(os.environ["STATE_DIR"])
    base = os.environ.get("MONITOR_URL", "http://api:8000").rstrip("/")
    key = os.getenv("MONITOR_KEY") or os.environ["API_KEY"]
    header = "X-Monitor-Key" if os.getenv("MONITOR_KEY") else "X-API-Key"
    pruned = 0
    while True:
        try:
            request = urllib.request.Request(base + "/ops/monitoring", headers={header: key})
            with urllib.request.urlopen(request, timeout=15) as response:
                snapshot = json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            snapshot = None
        update_alerts(store, {**alert_conditions(snapshot), **maintenance(store)})
        (store.directory / "monitor-heartbeat").write_text(str(time.time()))
        if time.time() - pruned > 86400:
            store.prune(int(os.environ.get("RETENTION_DAYS", "90")))
            with store.connect() as db:
                db.execute(
                    "DELETE FROM deliveries WHERE status='sent' AND delivered<?",
                    (time.time() - 30 * 86400,),
                )
            pruned = time.time()
        if args.once:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()

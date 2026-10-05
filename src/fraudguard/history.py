"""Single-organisation history; submitter ownership remains on predictions/audit."""

import json


def migrate(db):
    db.execute("BEGIN IMMEDIATE")
    if db.execute("SELECT 1 FROM settings WHERE key='shared_history_v1'").fetchone():
        return
    for row in db.execute(
        "SELECT e.*,p.at FROM behavioral_events e JOIN predictions p ON p.id=e.prediction_id ORDER BY p.at,e.prediction_id"
    ).fetchall():
        previous = db.execute(
            "SELECT raw FROM account_events WHERE event_id=?", (row["event_id"],)
        ).fetchone()
        if previous and json.loads(previous[0]) != json.loads(row["raw"]):
            raise ValueError(
                "Conflicting legacy event IDs across users; resolve from verified source before migration"
            )
        db.execute(
            "INSERT OR IGNORE INTO account_events VALUES(?,?,?,?,?,?,?,?)",
            (
                row["event_id"],
                row["account"],
                row["timestamp"],
                row["owner"],
                row["raw"],
                row["response"],
                row["prediction_id"],
                row["at"],
            ),
        )
    for row in db.execute(
        "SELECT * FROM behavioral_accounts ORDER BY timestamp,event_id"
    ).fetchall():
        db.execute(
            "INSERT INTO account_watermarks VALUES(?,?,?) ON CONFLICT(account) DO UPDATE SET timestamp=excluded.timestamp,event_id=excluded.event_id",
            (row["account"], row["timestamp"], row["event_id"]),
        )
    # Backfill only the currently known label at its actual availability timestamp.
    for row in db.execute(
        "SELECT l.*,e.raw FROM labels l JOIN account_events e ON e.prediction_id=l.prediction_id"
    ).fetchall():
        event = json.loads(row["raw"])
        db.execute(
            "INSERT INTO label_history(prediction_id,merchant,event_timestamp,fraud,available_at) VALUES(?,?,?,?,?)",
            (
                row["prediction_id"],
                event["merchant_id"],
                event["timestamp"],
                row["fraud"],
                row["at"],
            ),
        )
    db.execute("INSERT INTO settings VALUES('shared_history_v1','complete')")

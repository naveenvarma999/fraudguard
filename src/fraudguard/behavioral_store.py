"""One transactional, server-owned event path, shared with the review workspace."""

import hashlib
import json
import time
import uuid
from pathlib import Path

import numpy as np

from fraudguard.behavioral import WINDOW, Event
from fraudguard.behavioral_serving import score_event


def register_model(store, directory, manifest):
    artifact = (Path(directory) / "model.joblib").read_bytes()
    if hashlib.sha256(artifact).hexdigest() != manifest["model_sha256"]:
        raise ValueError("Behavioral artifact changed during registration")
    encoded = json.dumps(manifest, sort_keys=True)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT manifest,artifact FROM behavioral_models WHERE version=?",
            (manifest["version"],),
        ).fetchone()
        if row and (row["manifest"] != encoded or bytes(row["artifact"]) != artifact):
            raise ValueError("Behavioral model versions are immutable")
        db.execute(
            "INSERT OR IGNORE INTO behavioral_models VALUES(?,?,?)",
            (manifest["version"], encoded, artifact),
        )


def ingest(store, bundle, owner, payload):
    event = Event(**payload.transaction.model_dump())
    raw = json.dumps(payload.transaction.model_dump(), sort_keys=True)
    with store.connect() as db:
        # Serialize read-feature-score-write. A crash rolls all writes back.
        db.execute("BEGIN IMMEDIATE")
        retry = db.execute(
            "SELECT raw,response FROM behavioral_events WHERE owner=? AND event_id=?",
            (owner, event.event_id),
        ).fetchone()
        if retry:
            if retry["raw"] != raw:
                raise ValueError("Event ID already exists with a different payload")
            return json.loads(retry["response"])
        account = db.execute(
            "SELECT timestamp,event_id FROM behavioral_accounts WHERE owner=? AND account=?",
            (owner, event.account_id),
        ).fetchone()
        if account and (event.timestamp, event.event_id) <= (
            account["timestamp"],
            account["event_id"],
        ):
            raise ValueError("Late event: account events must arrive in timestamp/event_id order")
        records = db.execute(
            "SELECT raw FROM behavioral_events WHERE owner=? AND account=? AND timestamp>=? AND timestamp<? ORDER BY timestamp,event_id LIMIT 10001",
            (owner, event.account_id, event.timestamp - WINDOW, event.timestamp),
        ).fetchall()
        if len(records) > 10000:
            raise ValueError(
                "Account history exceeds the supported 30-day capacity; no event was stored"
            )
        history = [Event(**json.loads(row["raw"])) for row in records]
        result = score_event(bundle, event, history)
        result["history_source"] = "server-owned"
        result["prediction_id"] = uuid.uuid4().hex
        # Conservative fallback: no historical evidence never means auto-pass.
        if result["cold_start"]:
            result["decision"] = "review"
            result["decision_reason"] = "cold_start_manual_review"
            result["context"].append("Manual review required until account history exists")
        else:
            result["decision_reason"] = "model_threshold"
        now = time.time()
        db.execute(
            "INSERT INTO predictions(id,transaction_id,owner,at,model,amount,probability,threshold,decision) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                result["prediction_id"],
                event.event_id,
                owner,
                now,
                result["model_version"],
                event.amount,
                result["risk_score"],
                result["threshold"],
                result["decision"],
            ),
        )
        db.execute(
            "INSERT INTO behavioral_events VALUES(?,?,?,?,?,?,?)",
            (
                owner,
                event.event_id,
                event.account_id,
                event.timestamp,
                raw,
                json.dumps(result),
                result["prediction_id"],
            ),
        )
        db.execute(
            "INSERT INTO behavioral_accounts VALUES(?,?,?,?) ON CONFLICT(owner,account) DO UPDATE SET timestamp=excluded.timestamp,event_id=excluded.event_id",
            (owner, event.account_id, event.timestamp, event.event_id),
        )
        for feature, reference in bundle[1].get("reference", {}).items():
            counts = np.histogram(
                [result["features"][feature]], [-np.inf, *reference["inner_edges"], np.inf]
            )[0]
            previous = db.execute(
                "SELECT counts FROM bins WHERE model=? AND hour=? AND feature=?",
                (result["model_version"], int(now // 3600), feature),
            ).fetchone()
            if previous:
                counts += np.array(json.loads(previous["counts"]), dtype=int)
            db.execute(
                "INSERT INTO bins VALUES(?,?,?,?) ON CONFLICT(model,hour,feature) DO UPDATE SET counts=excluded.counts",
                (result["model_version"], int(now // 3600), feature, json.dumps(counts.tolist())),
            )
        store.audit(
            db,
            owner,
            "prediction.behavioral",
            result["prediction_id"],
            {"model": result["model_version"], "decision_reason": result["decision_reason"]},
        )
        return result

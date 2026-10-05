"""One transactional, server-owned event path, shared with the review workspace."""

import hashlib
import json
import time
import uuid
from pathlib import Path

import numpy as np

from fraudguard.behavioral import WINDOW, Event
from fraudguard.behavioral_serving import RawEvent, score_event
from fraudguard.enriched import BASE, ENRICHED, context, known_merchant


def base_event(raw):
    return Event(**{key: raw[key] for key in Event.__dataclass_fields__})


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
    from fraudguard.behavioral_lifecycle import active, saved_bundle

    bundle = active(store, bundle)
    event = base_event(payload.transaction.model_dump())
    raw = json.dumps(payload.transaction.model_dump(), sort_keys=True)
    with store.connect() as db:
        # Serialize read-feature-score-write. A crash rolls all writes back.
        db.execute("BEGIN IMMEDIATE")
        retry = db.execute(
            "SELECT raw,response FROM account_events WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        if retry:
            if (
                RawEvent(**json.loads(retry["raw"])).model_dump()
                != payload.transaction.model_dump()
            ):
                raise ValueError("Event ID already exists with a different payload")
            return json.loads(retry["response"])
        account = db.execute(
            "SELECT timestamp,event_id FROM account_watermarks WHERE account=?",
            (event.account_id,),
        ).fetchone()
        if account and (event.timestamp, event.event_id) <= (
            account["timestamp"],
            account["event_id"],
        ):
            result = {
                "status": "stored_late",
                "event_id": event.event_id,
                "prediction_id": None,
                "detail": "Saved for future account history; previous scores are unchanged",
            }
            db.execute(
                "INSERT INTO account_events VALUES(?,?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.account_id,
                    event.timestamp,
                    owner,
                    raw,
                    json.dumps(result),
                    None,
                    time.time(),
                ),
            )
            store.audit(db, owner, "event.late_retained", event.event_id)
            return result
        records = db.execute(
            "SELECT raw FROM account_events WHERE account=? AND timestamp>=? AND timestamp<? ORDER BY timestamp,event_id LIMIT 10001",
            (event.account_id, event.timestamp - WINDOW, event.timestamp),
        ).fetchall()
        if len(records) > 10000:
            raise ValueError(
                "Account history exceeds the supported 30-day capacity; no event was stored"
            )
        history = [base_event(json.loads(row["raw"])) for row in records]
        result = score_event(
            bundle,
            event,
            history,
            payload.transaction.model_dump(),
            known_merchant(db, event.merchant_id, event.timestamp),
        )
        # Capture enriched point-in-time inputs even while the v1 champion stays live.
        training_features = {name: result["features"][name] for name in BASE}
        training_features.update(context(payload.transaction.model_dump()))
        training_features.update(known_merchant(db, event.merchant_id, event.timestamp))
        result["training_features"] = {name: training_features[name] for name in ENRICHED}
        result["history_source"] = "server-owned"
        result["prediction_id"] = uuid.uuid4().hex
        # Conservative fallback: no historical evidence never means auto-pass.
        if result["cold_start"]:
            result["decision"] = "review"
            result["decision_reason"] = "cold_start_manual_review"
            result["context"].append("Manual review required until account history exists")
        elif bundle[1]["schema"] == "behavioral-v2" and (
            result["features"]["home_missing"] or result["features"]["category_unknown"]
        ):
            result["decision"] = "review"
            result["decision_reason"] = "missing_context_manual_review"
            result["context"].append(
                "Manual review required: category or account home location is missing"
            )
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
            "INSERT INTO account_events VALUES(?,?,?,?,?,?,?,?)",
            (
                event.event_id,
                event.account_id,
                event.timestamp,
                owner,
                raw,
                json.dumps(result),
                result["prediction_id"],
                now,
            ),
        )
        db.execute(
            "INSERT INTO account_watermarks VALUES(?,?,?) ON CONFLICT(account) DO UPDATE SET timestamp=excluded.timestamp,event_id=excluded.event_id",
            (event.account_id, event.timestamp, event.event_id),
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
        shadow = db.execute("SELECT value FROM settings WHERE key='behavioral_shadow'").fetchone()
        if shadow and shadow[0] != result["model_version"]:
            try:
                candidate = saved_bundle(str(store.directory), shadow[0])
                other = score_event(
                    candidate,
                    event,
                    history,
                    payload.transaction.model_dump(),
                    known_merchant(db, event.merchant_id, event.timestamp),
                )
                score, threshold, error = other["risk_score"], other["threshold"], None
            except Exception as exc:
                score, threshold, error = None, None, type(exc).__name__
            db.execute(
                "INSERT INTO shadow_scores VALUES(?,?,?,?,?)",
                (result["prediction_id"], shadow[0], score, threshold, error),
            )
        return result

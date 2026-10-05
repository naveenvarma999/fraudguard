"""Operator-registered candidates, paired shadow evidence and two-person promotion."""

import argparse
import hashlib
import io
import json
import sqlite3
import time
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss

from fraudguard.behavioral_serving import load_bundle


@lru_cache(maxsize=16)
def saved_bundle(directory, version):
    db = sqlite3.connect(Path(directory) / "workspace.sqlite3")
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            "SELECT manifest,artifact FROM behavioral_models WHERE version=?", (version,)
        ).fetchall()
    finally:
        db.close()
    if not rows:
        raise ValueError("Registered model not found")
    manifest = json.loads(rows[0]["manifest"])
    if hashlib.sha256(rows[0]["artifact"]).hexdigest() != manifest["model_sha256"]:
        raise ValueError("Registered model digest mismatch")
    return joblib.load(io.BytesIO(rows[0]["artifact"])), manifest


def active(store, fallback):
    rows = store.query("SELECT value FROM settings WHERE key='behavioral_active'")
    return saved_bundle(str(store.directory), rows[0]["value"]) if rows else fallback


def operator(db, name):
    if not db.execute(
        "SELECT 1 FROM users WHERE name=? AND role='admin' AND active=1", (name,)
    ).fetchone():
        raise ValueError("An active named administrator is required")


def submit(store, path, actor):
    from fraudguard.behavioral_store import register_model

    bundle = load_bundle(path)
    if bundle[1]["schema"] != "behavioral-v2" or not bundle[1].get("training_asof"):
        raise ValueError("A retrained v2 candidate with training cutoff is required")
    with store.connect() as db:
        operator(db, actor)
    register_model(store, path, bundle[1])
    with store.connect() as db:
        db.execute(
            "INSERT INTO behavioral_candidates VALUES(?,?,?,?,NULL)",
            (bundle[1]["version"], actor, time.time(), "pending"),
        )
        store.audit(db, actor, "behavioral.submitted", bundle[1]["version"])
    return bundle[1]["version"]


def start_shadow(store, version, actor):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        operator(db, actor)
        if not db.execute(
            "SELECT 1 FROM behavioral_candidates WHERE version=? AND status='pending'", (version,)
        ).fetchone():
            raise ValueError("Candidate must be pending")
        db.execute(
            "INSERT INTO settings VALUES('behavioral_shadow',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (version,),
        )
        store.audit(db, actor, "behavioral.shadow_started", version)


def evidence(store, version):
    rows = store.query(
        """SELECT s.*,p.probability,p.threshold AS live_threshold,l.fraud FROM shadow_scores s
       JOIN predictions p ON p.id=s.prediction_id LEFT JOIN labels l ON l.prediction_id=p.id WHERE s.version=? LIMIT 100001""",
        (version,),
    )
    if len(rows) > 100000:
        return {
            "paired": len(rows),
            "labeled": 0,
            "status": "window_too_large",
            "promotable": False,
        }
    labeled = [r for r in rows if r["fraud"] is not None and r["error"] is None]
    result = {
        "paired": len(rows),
        "errors": sum(r["error"] is not None for r in rows),
        "labeled": len(labeled),
        "status": "insufficient_labels",
        "promotable": False,
    }
    if (
        len(labeled) < 50
        or sum(r["fraud"] for r in labeled) < 5
        or sum(1 - r["fraud"] for r in labeled) < 5
    ):
        return result
    y = np.array([r["fraud"] for r in labeled])
    live, shadow = [np.array([r[name] for r in labeled]) for name in ("probability", "score")]
    result.update(
        status="measured",
        live_ap=float(average_precision_score(y, live)),
        shadow_ap=float(average_precision_score(y, shadow)),
        live_brier=float(brier_score_loss(y, live)),
        shadow_brier=float(brier_score_loss(y, shadow)),
    )
    result["promotable"] = (
        result["errors"] == 0
        and result["shadow_ap"] >= result["live_ap"] - 0.02
        and result["shadow_brier"] <= result["live_brier"] + 0.01
    )
    result["limits"] = (
        "Paired observed labels may be selective; minimum evidence gates do not prove banking performance"
    )
    return result


def promote(store, version, actor):
    # Keep gate reads and activation under the same write lock.
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        operator(db, actor)
        candidate = db.execute(
            "SELECT * FROM behavioral_candidates WHERE version=?", (version,)
        ).fetchone()
        if not candidate or candidate["status"] != "pending" or candidate["submitted_by"] == actor:
            raise ValueError("Approval requires a different named administrator")
        report = evidence(store, version)
        if not report["promotable"]:
            raise ValueError("Paired shadow quality gates have not passed")
        previous = db.execute("SELECT value FROM settings WHERE key='behavioral_active'").fetchone()
        db.execute(
            "INSERT INTO settings VALUES('behavioral_previous',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (previous[0] if previous else "__bundled__",),
        )
        db.execute(
            "INSERT INTO settings VALUES('behavioral_active',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (version,),
        )
        db.execute("DELETE FROM settings WHERE key='behavioral_shadow'")
        db.execute(
            "UPDATE behavioral_candidates SET status='approved',approved_by=? WHERE version=?",
            (actor, version),
        )
        store.audit(db, actor, "behavioral.promoted", version, report)


def rollback(store, actor):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        operator(db, actor)
        previous = db.execute(
            "SELECT value FROM settings WHERE key='behavioral_previous'"
        ).fetchone()
        if not previous:
            raise ValueError("No previous behavioral release")
        if previous[0] == "__bundled__":
            db.execute("DELETE FROM settings WHERE key='behavioral_active'")
        else:
            db.execute("UPDATE settings SET value=? WHERE key='behavioral_active'", (previous[0],))
        db.execute("DELETE FROM settings WHERE key='behavioral_previous'")
        store.audit(db, actor, "behavioral.rolled_back", previous[0])


if __name__ == "__main__":
    from fraudguard.store import Store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["submit", "shadow", "evidence", "promote", "rollback"])
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--actor")
    parser.add_argument("--model")
    parser.add_argument("--version")
    args = parser.parse_args()
    store = Store(args.state_dir)
    if args.action == "submit":
        result = submit(store, args.model, args.actor)
    elif args.action == "shadow":
        result = start_shadow(store, args.version, args.actor)
    elif args.action == "promote":
        result = promote(store, args.version, args.actor)
    elif args.action == "rollback":
        result = rollback(store, args.actor)
    else:
        result = evidence(store, args.version)
    print(json.dumps(result, indent=2))

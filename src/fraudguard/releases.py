"""Immutable local release registry with independent approval and atomic model switching."""

import hashlib
import json
import re
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from fraudguard.artifacts import load_bundle
from fraudguard.dashboard import ASSETS, prepare_demo
from fraudguard.data import FEATURES


def bundle_digest(path):
    digest = hashlib.sha256()
    for name in ("manifest.json", "model.joblib", "evaluation.json"):
        file = Path(path) / name
        digest.update(name.encode())
        digest.update(file.read_bytes() if file.exists() else b"absent")
    return digest.hexdigest()


def check_release(path):
    model, manifest = load_bundle(path)
    report = json.loads((Path(path) / "evaluation.json").read_text(encoding="utf-8-sig"))
    test = report["test"]
    threshold = manifest["policy"]["threshold"]
    rows = json.loads((ASSETS / "sample.json").read_text())["transactions"]
    probabilities = model.predict_proba(pd.DataFrame(rows)[FEATURES])[:, 1]
    checks = {
        "schema_and_checksum": True,
        "matching_dataset": report["dataset"]["sha256"] == manifest.get("dataset_sha256"),
        "matching_champion": report["champion"] == manifest["champion"],
        "matching_threshold": report["policy"]["threshold"] == threshold,
        "valid_threshold": 0 < threshold < 1,
        "finite_smoke_predictions": bool(
            np.isfinite(probabilities).all() and ((probabilities >= 0) & (probabilities <= 1)).all()
        ),
        "test_rows_at_least_1000": test["rows"] >= 1000,
        "test_frauds_at_least_30": test["frauds"] >= 30,
        "average_precision_at_least_060": test["average_precision"] >= 0.60,
        "recall_at_least_065": test["recall"] >= 0.65,
        "precision_at_least_030": test["precision"] >= 0.30,
        "review_rate_at_most_001": test["review_rate"] <= 0.01,
    }
    return model, manifest, {"passed": all(checks.values()), "checks": checks}


def stage(store, source, actor):
    users = store.query("SELECT role FROM users WHERE name=? AND active=1", (actor,))
    if not users or users[0]["role"] != "admin":
        raise ValueError("Submitter must be an active named administrator")
    source = Path(source).resolve()
    # Only trusted operator-provided files: loading a pickle can execute code.
    model, manifest, gates = check_release(source)
    version = manifest["run_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", version):
        raise ValueError("Invalid release identifier")
    destination = store.directory / "releases" / version
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=False)
    for name in ("model.joblib", "manifest.json", "evaluation.json"):
        shutil.copy2(source / name, destination / name)
    # Verify the copy, not just the original files (also catches concurrent modifications).
    _, copied_manifest, gates = check_release(destination)
    if copied_manifest["run_id"] != version:
        raise ValueError("Release changed during staging")
    with store.connect() as db:
        db.execute(
            "INSERT INTO releases VALUES(?,?,?,?,?,?,?, ?,NULL,NULL)",
            (
                version,
                str(destination.resolve()),
                bundle_digest(destination),
                json.dumps(copied_manifest),
                json.dumps(gates),
                actor,
                time.time(),
                "pending" if gates["passed"] else "rejected",
            ),
        )
        store.audit(db, actor, "release.staged", version, gates)
    return {"id": version, **gates}


class Runtime:
    def __init__(self, app, store, initial):
        self.app, self.store = app, store
        self.lock = threading.RLock()
        self.path = Path(initial)

    def restore(self):
        if not self.store:
            return
        rows = self.store.query("SELECT value FROM settings WHERE key='active_release'")
        if rows:
            release = self.store.query("SELECT * FROM releases WHERE id=?", (rows[0]["value"],))[0]
            self._load(release)
            return
        if self.app.state.model is None:
            return
        manifest = self.app.state.manifest
        version = manifest["run_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", version):
            raise ValueError("Invalid release identifier")
        # Keep the deployed baseline in durable storage so rollback survives image updates.
        destination = self.store.directory / "releases" / version
        destination.mkdir(parents=True, exist_ok=True)
        for name in ("model.joblib", "manifest.json", "evaluation.json"):
            if (self.path / name).exists():
                shutil.copy2(self.path / name, destination / name)
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO releases VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    version,
                    str(destination.resolve()),
                    bundle_digest(destination),
                    json.dumps(manifest),
                    json.dumps(
                        {
                            "passed": True,
                            "baseline": "Previously deployed model; not a new gated promotion",
                        }
                    ),
                    "bootstrap",
                    time.time(),
                    "approved",
                    "bootstrap",
                    time.time(),
                ),
            )
            db.execute("INSERT INTO settings VALUES(?,?)", ("active_release", version))
            self.store.audit(db, "bootstrap", "release.baseline", version)
        self.path = destination

    def _load(self, release):
        path = Path(release["path"])
        if release["status"] != "approved" or bundle_digest(path) != release["digest"]:
            raise ValueError("Release integrity or approval failed")
        model, manifest = load_bundle(path)
        demo = prepare_demo(model, manifest)
        self.app.state.model, self.app.state.manifest, self.app.state.demo = model, manifest, demo
        self.path = path

    def approve(self, version, actor):
        with self.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM releases WHERE id=?", (version,)).fetchone()
            if not row or row["status"] != "pending":
                raise ValueError("Only a pending release can be approved")
            if actor == row["submitted_by"]:
                raise ValueError("A different administrator must approve this release")
            if bundle_digest(row["path"]) != row["digest"]:
                raise ValueError("Release changed after staging")
            _, _, gates = check_release(row["path"])
            if not gates["passed"]:
                raise ValueError("Release gates failed")
            db.execute(
                "UPDATE releases SET status='approved',approved_by=?,approved_at=? WHERE id=?",
                (actor, time.time(), version),
            )
            self.store.audit(db, actor, "release.approved", version)

    def activate(self, version, actor, rollback=False):
        # Inference takes the same lock, so a batch always uses one model + policy + version.
        with self.lock:
            previous = self.store.query("SELECT value FROM settings WHERE key='active_release'")[0][
                "value"
            ]
            if rollback:
                rows = self.store.query("SELECT value FROM settings WHERE key='previous_release'")
                if not rows:
                    raise ValueError("No previous release to roll back to")
                version = rows[0]["value"]
            if version == previous:
                raise ValueError("Release is already active")
            rows = self.store.query("SELECT * FROM releases WHERE id=?", (version,))
            if not rows:
                raise ValueError("Unknown release")
            old = (self.app.state.model, self.app.state.manifest, self.app.state.demo, self.path)
            self._load(rows[0])
            try:
                with self.store.connect() as db:
                    db.execute(
                        "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        ("active_release", version),
                    )
                    db.execute(
                        "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        ("previous_release", previous),
                    )
                    self.store.audit(
                        db,
                        actor,
                        "release.rollback" if rollback else "release.activated",
                        version,
                        {"previous": previous},
                    )
            except Exception:
                self.app.state.model, self.app.state.manifest, self.app.state.demo, self.path = old
                raise
            return {"active": version, "previous": previous}

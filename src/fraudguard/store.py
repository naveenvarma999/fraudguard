"""Persistent workspace state. SQLite WAL is intended for one API replica."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY, password TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','analyst')), active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, username TEXT REFERENCES users(name), expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, started REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
 actor TEXT NOT NULL, action TEXT NOT NULL, resource TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'audit is append only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'audit is append only'); END;
CREATE TABLE IF NOT EXISTS predictions(id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL, owner TEXT NOT NULL,
 at REAL NOT NULL, model TEXT NOT NULL, amount REAL NOT NULL, probability REAL NOT NULL,
 threshold REAL NOT NULL, decision TEXT NOT NULL, review TEXT NOT NULL DEFAULT 'open', note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS prediction_owner_time ON predictions(owner,at);
CREATE INDEX IF NOT EXISTS prediction_model_time ON predictions(model,at);
CREATE TABLE IF NOT EXISTS labels(prediction_id TEXT PRIMARY KEY REFERENCES predictions(id) ON DELETE CASCADE,
 fraud INTEGER NOT NULL CHECK(fraud IN (0,1)), source TEXT NOT NULL, actor TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS bins(model TEXT NOT NULL, hour INTEGER NOT NULL, feature TEXT NOT NULL,
 counts TEXT NOT NULL, PRIMARY KEY(model,hour,feature));
CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY, path TEXT NOT NULL, digest TEXT NOT NULL,
 manifest TEXT NOT NULL, gates TEXT NOT NULL, submitted_by TEXT NOT NULL, submitted_at REAL NOT NULL,
 status TEXT NOT NULL, approved_by TEXT, approved_at REAL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(name TEXT PRIMARY KEY, active INTEGER NOT NULL, streak INTEGER NOT NULL,
 since REAL NOT NULL, checked REAL NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS session_activity(token TEXT PRIMARY KEY REFERENCES sessions(token) ON DELETE CASCADE,
 issued_at REAL NOT NULL, last_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt REAL NOT NULL DEFAULT 0, last_error TEXT, delivered REAL);
CREATE TABLE IF NOT EXISTS quotas(bucket TEXT PRIMARY KEY,tokens REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS mfa(username TEXT PRIMARY KEY REFERENCES users(name),secret TEXT NOT NULL,last_step INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS recovery_codes(username TEXT REFERENCES users(name),digest TEXT NOT NULL,PRIMARY KEY(username,digest));
CREATE TABLE IF NOT EXISTS enrollments(token TEXT PRIMARY KEY,username TEXT REFERENCES users(name),expires REAL NOT NULL,secret TEXT,attempts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS behavioral_models(version TEXT PRIMARY KEY,manifest TEXT NOT NULL,artifact BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS behavioral_accounts(owner TEXT NOT NULL,account TEXT NOT NULL,timestamp INTEGER NOT NULL,event_id TEXT NOT NULL,PRIMARY KEY(owner,account));
CREATE TABLE IF NOT EXISTS behavioral_events(owner TEXT NOT NULL,event_id TEXT NOT NULL,account TEXT NOT NULL,
 timestamp INTEGER NOT NULL,raw TEXT NOT NULL,response TEXT NOT NULL,prediction_id TEXT NOT NULL REFERENCES predictions(id) ON DELETE CASCADE,PRIMARY KEY(owner,event_id));
CREATE INDEX IF NOT EXISTS behavioral_account_time ON behavioral_events(owner,account,timestamp,event_id);
PRAGMA user_version=4;
"""


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "workspace.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def query(self, sql, params=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, params)]

    @staticmethod
    def audit(db, actor, action, resource="", detail=None):
        db.execute(
            "INSERT INTO audit(at,actor,action,resource,detail) VALUES(?,?,?,?,?)",
            (time.time(), actor, action, resource, json.dumps(detail or {})),
        )

    def record(self, actor, rows, scores, manifest):
        """Persist scores and aggregate feature bins atomically; no raw feature vectors."""
        import numpy as np

        from fraudguard.data import FEATURES

        now = time.time()
        hour = int(now // 3600)
        ids = [uuid.uuid4().hex for _ in rows]
        version = manifest["run_id"]
        threshold = manifest["policy"]["threshold"]
        with self.connect() as db:
            db.executemany(
                "INSERT INTO predictions(id,transaction_id,owner,at,model,amount,probability,threshold,decision) VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    (
                        pid,
                        r["transaction_id"],
                        actor,
                        now,
                        version,
                        r["Amount"],
                        float(p),
                        threshold,
                        "review" if p >= threshold else "pass",
                    )
                    for pid, r, p in zip(ids, rows, scores)
                ],
            )
            for feature in FEATURES:
                reference = manifest["reference"][feature]
                counts = np.histogram(
                    [r[feature] for r in rows], [-np.inf, *reference["inner_edges"], np.inf]
                )[0]
                old = db.execute(
                    "SELECT counts FROM bins WHERE model=? AND hour=? AND feature=?",
                    (version, hour, feature),
                ).fetchone()
                if old:
                    counts += np.array(json.loads(old["counts"]), dtype=int)
                db.execute(
                    "INSERT INTO bins VALUES(?,?,?,?) ON CONFLICT(model,hour,feature) DO UPDATE SET counts=excluded.counts",
                    (version, hour, feature, json.dumps(counts.tolist())),
                )
            self.audit(
                db, actor, "prediction.batch", version, {"rows": len(rows), "prediction_ids": ids}
            )
        return ids

    def backup(self, destination):
        path = Path(destination)
        if path.exists():
            raise ValueError("Backup destination already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(path)
        try:
            with self.connect() as db:
                db.backup(target)
        finally:
            target.close()

    def prune(self, days=90):
        if days < 7:
            raise ValueError("Retention must be at least 7 days")
        cutoff = time.time() - days * 86400
        with self.connect() as db:
            if days < 30 and db.execute("SELECT 1 FROM behavioral_events LIMIT 1").fetchone():
                raise ValueError("Behavioral history requires at least 30 days of retention")
            deleted = db.execute("DELETE FROM predictions WHERE at<?", (cutoff,)).rowcount
            db.execute("DELETE FROM bins WHERE hour<?", (int(cutoff // 3600),))
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute("DELETE FROM attempts WHERE started<?", (time.time() - 3600,))
            self.audit(
                db, "operator", "retention.prune", detail={"days": days, "predictions": deleted}
            )
        return deleted

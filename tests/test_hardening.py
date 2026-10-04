import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fraudguard.api import create_app
from fraudguard.auth import add_user
from fraudguard.backups import create_backup, restore, verify
from fraudguard.notifications import deliver, enqueue, validate_url
from fraudguard.store import Store

KEY = "hardening-service-test-key"
PASSWORD = "hardening-test-password"


@pytest.fixture
def secured(bundle, tmp_path):
    state = tmp_path / "state"
    store = Store(state)
    add_user(store, "admin", PASSWORD, "admin")
    with TestClient(create_app(bundle[0], KEY, state)) as client:
        response = client.post("/auth/login", json={"username": "admin", "password": PASSWORD})
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        yield client, store, headers


def test_idle_timeout_and_recent_admin_authentication(secured):
    client, store, headers = secured
    with store.connect() as db:
        db.execute("UPDATE session_activity SET issued_at=?", (time.time() - 901,))
    assert client.get("/ops/users", headers=headers).status_code == 200
    assert (
        client.post(
            "/ops/users",
            headers=headers,
            json={"username": "new-user", "password": PASSWORD, "role": "analyst"},
        ).status_code
        == 401
    )
    with store.connect() as db:
        db.execute("UPDATE session_activity SET last_seen=?", (time.time() - 1801,))
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_scoped_monitor_key_cannot_score_or_manage_users(secured, monkeypatch, payload):
    client, _, _ = secured
    monkeypatch.setenv("MONITOR_KEY", "read-only-monitor-test-key")
    headers = {"X-Monitor-Key": "read-only-monitor-test-key"}
    assert client.get("/ops/monitoring", headers=headers).status_code == 200
    assert client.post("/v1/predict", headers=headers, json=payload).status_code == 401
    assert client.get("/ops/users", headers=headers).status_code == 401
    assert client.get("/workspace").headers["X-Frame-Options"] == "DENY"


def test_full_backup_restore_preserves_history_models_and_revokes_sessions(
    secured, bundle, payload, tmp_path
):
    client, store, headers = secured
    result = client.post("/v1/predict", headers=headers, json=payload).json()
    pid = result["predictions"][0]["prediction_id"]
    client.post(
        f"/ops/predictions/{pid}/label",
        headers=headers,
        json={"fraud": 1, "source": "verified test evidence"},
    )
    backup = create_backup(store, tmp_path / "backups")
    archive = tmp_path / "backups" / backup["file"]
    assert verify(archive, tmp_path / "scratch")["sessions"] == "revoked in snapshot"
    target = tmp_path / "recovered"
    restore(archive, target)
    recovered = Store(target)
    assert len(recovered.query("SELECT * FROM predictions")) == 1
    assert recovered.query("SELECT fraud FROM labels")[0]["fraud"] == 1
    assert recovered.query("SELECT * FROM sessions") == []
    with TestClient(create_app(bundle[0], KEY, target)) as restored:
        assert restored.get("/health/ready").status_code == 200
        assert restored.get("/auth/me", headers=headers).status_code == 401
        fresh = restored.post("/auth/login", json={"username": "admin", "password": PASSWORD})
        assert fresh.status_code == 200
        assert restored.get("/ui/demo").json()["model_version"] == result["model_version"]
    with pytest.raises(ValueError, match="new directory"):
        restore(archive, target)


def test_corrupt_backup_and_path_traversal_are_rejected(secured, tmp_path):
    _, store, _ = secured
    result = create_backup(store, tmp_path / "backups")
    source = tmp_path / "backups" / result["file"]
    for name in ["checksum", "traversal"]:
        bad = tmp_path / (name + ".zip")
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(bad, "w") as changed:
            for file in original.namelist():
                content = original.read(file)
                if name == "checksum" and file == "workspace.sqlite3":
                    content += b"corruption"
                changed.writestr(file, content)
            if name == "traversal":
                changed.writestr("../escaped.txt", "unsafe")
        with pytest.raises(ValueError):
            restore(bad, tmp_path / ("restore-" + name))
        assert not (tmp_path / ("restore-" + name)).exists()
        assert not (tmp_path / "escaped.txt").exists()


def test_backup_retention_and_failure_do_not_replace_last_good(secured, tmp_path):
    _, store, _ = secured
    root = tmp_path / "backups"
    create_backup(store, root, keep=2)
    create_backup(store, root, keep=2)
    latest = create_backup(store, root, keep=2)
    assert len(list(root.glob("backup-*.zip"))) == 2
    assert (root / latest["file"]).exists()
    row = store.query("SELECT path FROM releases")[0]
    with (Path(row["path"]) / "model.joblib").open("ab") as f:
        f.write(b"corrupt")
    with pytest.raises(ValueError):
        create_backup(store, root, keep=2)
    assert (
        json.loads(store.query("SELECT value FROM settings WHERE key='backup_latest'")[0]["value"])[
            "file"
        ]
        == latest["file"]
    )
    assert not list(root.glob(".building-*"))


def test_notification_queue_retry_delivery_and_secret_redaction(tmp_path):
    store = Store(tmp_path / "state")
    with store.connect() as db:
        enqueue(db, "service_unavailable", True, "API check failed", time.time())
    assert deliver(store, url="")["enabled"] is False

    def failure(*args):
        raise RuntimeError("do not log https://receiver.example/secret-token")

    deliver(store, url="https://receiver.example", sender=failure)
    row = store.query("SELECT * FROM deliveries")[0]
    assert row["attempts"] == 1 and row["last_error"] == "RuntimeError"
    assert "secret-token" not in json.dumps(row)
    assert deliver(store, url="https://receiver.example", sender=lambda *args: None)["sent"] == 0
    with store.connect() as db:
        db.execute("UPDATE deliveries SET next_attempt=0")
    received = []
    assert (
        deliver(
            store,
            url="https://receiver.example",
            sender=lambda url, payload, token: received.append(payload),
        )["sent"]
        == 1
    )
    assert received[0]["event_id"] == row["id"]
    assert store.query("SELECT status FROM deliveries")[0]["status"] == "sent"
    assert deliver(store, url="https://receiver.example", sender=lambda *args: None)["sent"] == 0


def test_notification_exhaustion_and_https_requirement(tmp_path):
    store = Store(tmp_path / "state")
    with store.connect() as db:
        enqueue(db, "test", True, "test event", time.time())
        db.execute("UPDATE deliveries SET attempts=7")

    def failure(*args):
        raise OSError("offline")

    deliver(store, url="https://receiver.example", sender=failure)
    assert store.query("SELECT status FROM deliveries")[0]["status"] == "dead"
    for url in [
        "http://receiver.example",
        "https://user:secret@example.com",
        "https://example.com/#secret",
    ]:
        with pytest.raises(ValueError):
            validate_url(url)


def test_deployment_checks_reject_wrong_version_and_missing_backup(secured, monkeypatch, tmp_path):
    import urllib.request
    from urllib.parse import urlsplit

    from fraudguard.deployment import check

    client, store, _ = secured

    class Reply:
        def __init__(self, response):
            self.status = response.status_code
            self.headers = response.headers
            self.content = response.content

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return self.content

    def transport(request, timeout):
        return Reply(
            client.get(urlsplit(request.full_url).path, headers=dict(request.header_items()))
        )

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    assert check("http://local.test", "2.5.0", KEY)["status"] == "passed"
    with pytest.raises(ValueError, match="version"):
        check("http://local.test", "incorrect", KEY)
    with pytest.raises(ValueError, match="backup"):
        check("http://local.test", "2.5.0", KEY, require_backup=True)
    create_backup(store, tmp_path / "backups")
    assert check("http://local.test", "2.5.0", KEY, require_backup=True)["status"] == "passed"


def test_backup_schedule_survives_monitor_restart(secured, tmp_path, monkeypatch):
    from fraudguard.watchdog import maintenance

    _, store, _ = secured
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "scheduled"))
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    assert not maintenance(store)["backup_stale"][0]
    first = list((tmp_path / "scheduled").glob("backup-*.zip"))
    assert len(first) == 1
    assert not maintenance(Store(store.directory))["backup_stale"][0]
    assert list((tmp_path / "scheduled").glob("backup-*.zip")) == first


def test_monitor_secret_cannot_equal_service_secret(bundle, tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_KEY", KEY)
    with pytest.raises(RuntimeError, match="distinct"):
        with TestClient(create_app(bundle[0], KEY, tmp_path / "state")):
            pass


def test_process_crash_rolls_back_uncommitted_writes(secured):
    import subprocess
    import sys

    _, store, _ = secured
    before = len(store.query("SELECT * FROM audit"))
    script = """
import os, sqlite3, sys, time
connection=sqlite3.connect(sys.argv[1])
connection.execute('BEGIN IMMEDIATE')
connection.execute('INSERT INTO audit(at,actor,action,resource,detail) VALUES(?,?,?,?,?)',(time.time(),'crash-test','uncommitted','','{}'))
os._exit(7)
"""
    result = subprocess.run([sys.executable, "-c", script, str(store.path)], check=False)
    assert result.returncode == 7
    reopened = Store(store.directory)
    assert len(reopened.query("SELECT * FROM audit")) == before
    assert reopened.query("PRAGMA integrity_check")[0]["integrity_check"] == "ok"


def test_firing_alert_does_not_falsely_resolve_after_monitor_restart(tmp_path):
    from fraudguard.operations import update_alerts

    store = Store(tmp_path / "state")
    condition = {"service_unavailable": (True, "API unavailable")}
    update_alerts(store, condition)
    update_alerts(store, condition)
    with store.connect() as db:
        db.execute("UPDATE alerts SET checked=?", (time.time() - 600,))
    update_alerts(store, condition)
    assert store.query("SELECT active FROM alerts")[0]["active"] == 1
    assert not store.query("SELECT * FROM audit WHERE action='alert.resolved'")
    update_alerts(store, {"service_unavailable": (False, "API recovered")})
    assert store.query("SELECT active FROM alerts")[0]["active"] == 0


def test_latest_backup_verification_detects_removed_or_changed_archive(secured, tmp_path):
    from fraudguard.backups import verify_latest

    _, store, _ = secured
    root = tmp_path / "backups"
    result = create_backup(store, root)
    assert verify_latest(store, root)["verified"]
    with (root / result["file"]).open("ab") as stream:
        stream.write(b"changed after verification")
    with pytest.raises(ValueError, match="changed"):
        verify_latest(store, root)

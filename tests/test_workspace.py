import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from fraudguard.api import create_app
from fraudguard.auth import add_user
from fraudguard.store import Store

KEY = "workspace-service-test-key"
PASSWORD = "workspace-password-for-tests"


@pytest.fixture
def workspace(bundle, tmp_path):
    path = tmp_path / "state"
    store = Store(path)
    for name, role in [("admin", "admin"), ("reviewer", "analyst"), ("other", "analyst")]:
        add_user(store, name, PASSWORD, role)
    with TestClient(create_app(bundle[0], KEY, path)) as client:
        yield client, store


def signin(client, username="admin"):
    response = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_accounts_ownership_reviews_labels_and_audit(workspace, payload):
    client, store = workspace
    analyst, other, admin = signin(client, "reviewer"), signin(client, "other"), signin(client)
    assert client.get("/workspace").status_code == 200
    assert "frame-ancestors" in client.get("/workspace").headers["content-security-policy"]
    assert client.get("/ops/users", headers=analyst).status_code == 403
    assert client.get("/ops/audit", headers={"X-API-Key": KEY}).status_code == 401
    response = client.post("/v1/predict", headers=analyst, json=payload)
    assert response.status_code == 200, response.text
    identifier = response.json()["predictions"][0]["prediction_id"]
    own = client.get("/ops/predictions", headers=analyst).json()
    assert len(own) == 1 and own[0]["owner"] == "reviewer"
    assert client.get("/ops/predictions", headers=other).json() == []
    assert len(client.get("/ops/predictions", headers=admin).json()) == 1
    review = {"disposition": "cleared", "note": "Reviewed supporting evidence"}
    assert (
        client.post(f"/ops/predictions/{identifier}/review", headers=other, json=review).status_code
        == 404
    )
    assert (
        client.post(
            f"/ops/predictions/{identifier}/review", headers=analyst, json=review
        ).status_code
        == 200
    )
    assert store.query("SELECT * FROM labels") == []  # Review opinion is not ground truth.
    for fraud in (1, 0):
        assert (
            client.post(
                f"/ops/predictions/{identifier}/label",
                headers=analyst,
                json={"fraud": fraud, "source": "confirmed case ABC"},
            ).status_code
            == 200
        )
    events = client.get("/ops/audit", headers=analyst).json()
    assert all(e["actor"] == "reviewer" for e in events)
    correction = next(e for e in events if e["action"] == "label.corrected")
    assert json.loads(correction["detail"])["previous"]["fraud"] == 1
    assert not any("V1" in r.keys() for r in own)
    with pytest.raises(sqlite3.IntegrityError), store.connect() as db:
        db.execute("DELETE FROM audit")


def test_revocation_disabled_users_and_no_plaintext_secrets(workspace):
    client, store = workspace
    admin, analyst = signin(client), signin(client, "reviewer")
    assert (
        client.patch("/ops/users/reviewer", headers=admin, json={"active": False}).status_code
        == 200
    )
    assert client.get("/auth/me", headers=analyst).status_code == 401
    assert (
        client.post("/auth/login", json={"username": "reviewer", "password": PASSWORD}).status_code
        == 401
    )
    assert (
        client.patch("/ops/users/admin", headers=admin, json={"active": False}).status_code == 400
    )
    assert PASSWORD not in json.dumps(store.query("SELECT * FROM users"))
    assert admin["Authorization"][7:] not in json.dumps(store.query("SELECT * FROM sessions"))
    assert client.post("/auth/logout", headers=admin).status_code == 200
    assert client.get("/auth/me", headers=admin).status_code == 401


def test_password_change_revokes_all_sessions(workspace):
    client, _ = workspace
    first, second = signin(client), signin(client)
    assert (
        client.post(
            "/auth/password",
            headers=first,
            json={"current_password": PASSWORD, "new_password": "new-strong-test-password"},
        ).status_code
        == 200
    )
    assert client.get("/auth/me", headers=second).status_code == 401
    assert (
        client.post(
            "/auth/login", json={"username": "admin", "password": "new-strong-test-password"}
        ).status_code
        == 200
    )


def test_login_throttle_and_expiry(workspace, monkeypatch):
    client, store = workspace
    # Short backoff must be tested with a controlled clock, not CPU/hash speed.
    monkeypatch.setattr("fraudguard.auth.time.time", lambda: 1700000000.0)
    for _ in range(5):
        assert (
            client.post(
                "/auth/login", json={"username": "missing", "password": "incorrect"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/auth/login", json={"username": "missing", "password": "incorrect"}
        ).status_code
        == 429
    )
    headers = signin(client)
    with store.connect() as db:
        db.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_pagination_does_not_skip_transactions_from_same_batch(workspace, payload):
    client, _ = workspace
    headers = signin(client)
    payload["transactions"] = [
        dict(payload["transactions"][0], transaction_id=f"txn-{i}") for i in range(100)
    ]
    assert client.post("/v1/predict", headers=headers, json=payload).status_code == 200
    first = client.get("/ops/predictions", headers=headers).json()
    second = client.get("/ops/predictions?before=" + first[-1]["id"], headers=headers).json()
    assert len({p["id"] for p in first + second}) == 100


def test_database_restart_backup_and_retention(workspace, payload, tmp_path):
    client, store = workspace
    headers = signin(client)
    client.post("/v1/predict", headers=headers, json=payload)
    assert len(Store(store.directory).query("SELECT * FROM predictions")) == 1
    backup = tmp_path / "backup.sqlite3"
    store.backup(backup)
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT count(*) FROM predictions").fetchone()[0] == 1
    with store.connect() as db:
        db.execute("UPDATE predictions SET at=?", (time.time() - 91 * 86400,))
    assert store.prune() == 1
    assert store.query("SELECT * FROM audit WHERE action='prediction.batch'")


def test_disabled_persistence_reports_unavailable(bundle):
    with TestClient(create_app(bundle[0], KEY)) as client:
        assert (
            client.post("/auth/login", json={"username": "admin", "password": PASSWORD}).status_code
            == 503
        )


def test_concurrent_batch_accounting_has_no_lost_updates(workspace, payload):
    from concurrent.futures import ThreadPoolExecutor

    client, store = workspace
    headers = signin(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: client.post("/v1/predict", headers=headers, json=payload), range(20))
        )
    assert all(response.status_code == 200 for response in responses)
    assert len(store.query("SELECT * FROM predictions")) == 20
    assert (
        sum(json.loads(store.query("SELECT counts FROM bins WHERE feature='V1'")[0]["counts"]))
        == 20
    )

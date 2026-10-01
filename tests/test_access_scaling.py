import base64
import concurrent.futures
import time

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

from fraudguard import mfa
from fraudguard.api import create_app
from fraudguard.auth import add_user, login
from fraudguard.backups import create_backup, restore
from fraudguard.limits import quota
from fraudguard.pool import Pool
from fraudguard.releases import bundle_digest
from fraudguard.store import Store
from fraudguard.worker import create_worker

KEY = "test-scaling-service-key"
WORKER_KEY = "test-private-worker-key-with-32-characters"
PASSWORD = "local-test-password"


@pytest.fixture
def secured(bundle, tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_MFA", "true")
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    store = Store(tmp_path / "state")
    add_user(store, "admin", PASSWORD, "admin")
    with TestClient(create_app(bundle[0], KEY, store.directory)) as client:
        yield client, store


def enroll(client):
    response = client.post("/auth/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 200
    assert "access_token" not in response.json()
    token = response.json()["enrollment_token"]
    setup = client.post("/auth/mfa/setup", json={"token": token}).json()
    secret = setup["secret"]
    code = mfa.totp(secret, int(time.time() // 30))
    response = client.post("/auth/mfa/confirm", json={"token": token, "code": code})
    assert response.status_code == 200
    return secret, code, response.json()["recovery_codes"], token


def test_rfc6238_sha1_vectors():
    secret = base64.b32encode(b"12345678901234567890").decode()
    for seconds, expected in [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1234567890, "89005924"),
        (20000000000, "65353130"),
    ]:
        assert mfa.totp(secret, seconds // 30, digits=8) == expected


def test_enrollment_replay_recovery_and_encrypted_storage(secured):
    client, store = secured
    secret, code, recovery, token = enroll(client)
    saved = store.query("SELECT * FROM mfa")[0]
    assert secret not in saved["secret"]
    assert mfa.cipher().decrypt(saved["secret"].encode()).decode() == secret
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    credentials = {"username": "admin", "password": PASSWORD}
    assert client.post("/auth/login", json=credentials).status_code == 401
    assert client.post("/auth/login", json={**credentials, "otp": code}).status_code == 401
    response = client.post("/auth/login", json={**credentials, "otp": recovery[0]})
    assert response.status_code == 200
    assert (
        client.get(
            "/auth/me", headers={"Authorization": "Bearer " + response.json()["access_token"]}
        ).status_code
        == 200
    )
    assert client.post("/auth/login", json={**credentials, "otp": recovery[0]}).status_code == 401
    assert client.post("/auth/mfa/setup", json={"token": token}).status_code == 401


def test_totp_new_code_and_concurrent_replay(secured, monkeypatch):
    client, store = secured
    secret, _, _, _ = enroll(client)
    future = (int(time.time() // 30) + 2) * 30
    monkeypatch.setattr(mfa.time, "time", lambda: future)
    code = mfa.totp(secret, future // 30)

    def attempt(_):
        try:
            login(store, "admin", PASSWORD, "local", code)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [200, 401]


def test_enrollment_attempt_limit_and_expiry(secured):
    client, store = secured
    token = client.post("/auth/login", json={"username": "admin", "password": PASSWORD}).json()[
        "enrollment_token"
    ]
    secret = client.post("/auth/mfa/setup", json={"token": token}).json()["secret"]
    current = int(time.time() // 30)
    valid = {mfa.totp(secret, t) for t in (current - 1, current, current + 1)}
    bad = next(str(n).zfill(6) for n in range(10) if str(n).zfill(6) not in valid)
    for _ in range(5):
        assert (
            client.post("/auth/mfa/confirm", json={"token": token, "code": bad}).status_code == 400
        )
    assert (
        client.post(
            "/auth/mfa/confirm", json={"token": token, "code": mfa.totp(secret, current)}
        ).status_code
        == 401
    )
    with store.connect() as db:
        db.execute("UPDATE enrollments SET attempts=0,expires=0")
    assert client.post("/auth/mfa/setup", json={"token": token}).status_code == 401


def test_backup_restores_mfa_and_removes_enrollment_tokens(secured, tmp_path):
    client, store = secured
    _, _, recovery, _ = enroll(client)
    add_user(store, "pending", PASSWORD, "analyst")
    mfa.challenge(store, "pending")
    backup = create_backup(store, tmp_path / "backups")
    restored = tmp_path / "restored"
    restore(tmp_path / "backups" / backup["file"], restored)
    copy = Store(restored)
    assert copy.query("SELECT * FROM enrollments") == []
    assert login(copy, "admin", PASSWORD, "local", recovery[0])["access_token"]


def test_account_quota_concurrent_requests_and_refill(tmp_path):
    store = Store(tmp_path / "state")

    def take(_):
        try:
            quota(store, "alice", 1, now=100)
            return 200
        except HTTPException as exc:
            assert exc.headers["Retry-After"] == "1"
            return exc.status_code

    with concurrent.futures.ThreadPoolExecutor(12) as pool:
        results = list(pool.map(take, range(80)))
    assert results.count(200) == 60
    assert results.count(429) == 20
    quota(store, "bob", 1, now=100)
    quota(store, "alice", 1, now=101)
    with pytest.raises(HTTPException):
        quota(Store(store.directory), "alice", 1, now=101)


def test_transaction_budget_and_api_429(bundle, tmp_path, payload):
    store = Store(tmp_path / "state")
    quota(store, "service", 3000, now=time.time())
    with TestClient(create_app(bundle[0], KEY, store.directory)) as client:
        with store.connect() as db:
            db.execute("UPDATE quotas SET tokens=0,updated=?", (time.time() + 60,))
        response = client.post("/v1/predict", headers={"X-API-Key": KEY}, json=payload)
        assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0
        assert store.query("SELECT * FROM predictions") == []


def test_workers_balance_failover_and_full_outage(bundle, tmp_path, payload, monkeypatch):
    monkeypatch.setenv("INFERENCE_WORKERS", "http://worker-a,http://worker-b")
    monkeypatch.setenv("INFERENCE_KEY", WORKER_KEY)
    store = Store(tmp_path / "state")
    app = create_app(bundle[0], KEY, store.directory)
    with (
        TestClient(app) as client,
        TestClient(create_worker(store.directory / "releases", WORKER_KEY)) as worker_a,
        TestClient(create_worker(store.directory / "releases", WORKER_KEY)) as worker_b,
    ):
        workers = {"http://worker-a": worker_a, "http://worker-b": worker_b}

        def send(url, body):
            response = workers[url].post(
                "/internal/score", json=body, headers={"X-Inference-Key": WORKER_KEY}
            )
            response.raise_for_status()
            return response.json()

        app.state.pool.transport = send
        for _ in range(4):
            assert (
                client.post("/v1/predict", headers={"X-API-Key": KEY}, json=payload).status_code
                == 200
            )
        assert [r["completed"] for r in app.state.pool.status()] == [2, 2]
        del workers["http://worker-a"]
        assert (
            client.post("/v1/predict", headers={"X-API-Key": KEY}, json=payload).status_code == 200
        )
        assert len(store.query("SELECT * FROM predictions")) == 5
        workers.clear()
        response = client.post("/v1/predict", headers={"X-API-Key": KEY}, json=payload)
        assert response.status_code == 503
        assert len(store.query("SELECT * FROM predictions")) == 5


def test_worker_auth_integrity_and_path_validation(bundle, payload):
    registry = bundle[0].parent
    with TestClient(create_worker(registry, WORKER_KEY)) as client:
        body = {**payload, "version": "release", "digest": bundle_digest(bundle[0])}
        assert client.post("/internal/score", json=body).status_code == 401
        headers = {"X-Inference-Key": WORKER_KEY}
        assert (
            client.post(
                "/internal/score", headers=headers, json={**body, "version": "../release"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/internal/score", headers=headers, json={**body, "digest": "0" * 64}
            ).status_code
            == 503
        )
        assert (
            client.post("/internal/score", headers=headers, json=body).status_code == 503
        )  # Manifest version mismatches directory.


def test_pool_rejects_bad_scores_and_recovers(monkeypatch):
    now = [1.0]
    monkeypatch.setattr("fraudguard.pool.time.monotonic", lambda: now[0])
    pool = Pool(
        "http://one", WORKER_KEY, lambda *_: {"version": "v1", "probabilities": [float("nan")]}
    )
    with pytest.raises(HTTPException):
        pool.score([{}], "v1", "0" * 64)
    assert pool.status()[0]["circuit_open"]
    pool.transport = lambda *_: {"version": "v1", "probabilities": [0.25]}
    now[0] = 12
    assert pool.score([{}], "v1", "0" * 64) == [0.25]


def test_workspace_controls_exist_in_markup():
    import re

    from fraudguard.dashboard import ASSETS

    script = (ASSETS / "workspace.mjs").read_text(encoding="utf-8")
    markup = (ASSETS / "workspace.html").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\('([^']+)'\)", script))
    present = set(re.findall(r'id="([^"]+)"', markup))
    assert used <= present, used - present


def test_inflight_batch_keeps_its_release_during_activation(bundle, tmp_path, payload, monkeypatch):
    import json
    import shutil

    monkeypatch.setenv("INFERENCE_WORKERS", "http://worker-a")
    monkeypatch.setenv("INFERENCE_KEY", WORKER_KEY)
    store = Store(tmp_path / "state")
    app = create_app(bundle[0], KEY, store.directory)
    with (
        TestClient(app) as client,
        TestClient(create_worker(store.directory / "releases", WORKER_KEY)) as worker,
    ):
        initial = store.query("SELECT * FROM releases")[0]
        destination = store.directory / "releases" / "test-v2"
        shutil.copytree(initial["path"], destination)
        manifest = json.loads((destination / "manifest.json").read_text())
        manifest["run_id"] = "test-v2"
        manifest["policy"]["threshold"] = 0.9
        (destination / "manifest.json").write_text(json.dumps(manifest))
        with store.connect() as db:
            db.execute(
                "INSERT INTO releases VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "test-v2",
                    str(destination.resolve()),
                    bundle_digest(destination),
                    json.dumps(manifest),
                    "{}",
                    "test",
                    0,
                    "approved",
                    "test",
                    0,
                ),
            )
        switched = False

        def send(url, body):
            nonlocal switched
            if not switched:
                app.state.runtime.activate("test-v2", "test")
                switched = True
            response = worker.post(
                "/internal/score", json=body, headers={"X-Inference-Key": WORKER_KEY}
            )
            response.raise_for_status()
            return response.json()

        app.state.pool.transport = send
        first = client.post("/v1/predict", headers={"X-API-Key": KEY}, json=payload).json()
        second = client.post("/v1/predict", headers={"X-API-Key": KEY}, json=payload).json()
        assert (first["model_version"], first["threshold"]) == ("test-v1", 0.5)
        assert (second["model_version"], second["threshold"]) == ("test-v2", 0.9)
        assert {p["model"] for p in store.query("SELECT model FROM predictions")} == {
            "test-v1",
            "test-v2",
        }

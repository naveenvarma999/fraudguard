import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fraudguard.api import create_app
from fraudguard.auth import add_user
from fraudguard.releases import stage
from fraudguard.store import Store

BUNDLE = Path(__file__).resolve().parents[1] / "artifacts/benchmark"
KEY = "release-test-service-key"
PASSWORD = "release-test-password"


def auth(client, username):
    result = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    assert result.status_code == 200
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def candidate(tmp_path, name="candidate-v2", fail=False):
    path = tmp_path / name
    path.mkdir()
    for filename in ("model.joblib", "manifest.json", "evaluation.json"):
        shutil.copy2(BUNDLE / filename, path / filename)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["run_id"] = name
    (path / "manifest.json").write_text(json.dumps(manifest))
    if fail:
        report = json.loads((path / "evaluation.json").read_text())
        report["test"]["recall"] = 0.1
        (path / "evaluation.json").write_text(json.dumps(report))
    return path


def test_independent_approval_activation_restart_and_rollback(tmp_path):
    state = tmp_path / "state"
    store = Store(state)
    add_user(store, "submitter", PASSWORD, "admin")
    add_user(store, "approver", PASSWORD, "admin")
    with TestClient(create_app(BUNDLE, KEY, state)) as client:
        first, second = auth(client, "submitter"), auth(client, "approver")
        baseline = client.get("/ops/releases", headers=first).json()["active"]
        result = stage(store, candidate(tmp_path), "submitter")
        assert result["passed"], result
        assert client.post("/ops/releases/candidate-v2/activate", headers=first).status_code == 409
        assert client.post("/ops/releases/candidate-v2/approve", headers=first).status_code == 409
        assert client.post("/ops/releases/candidate-v2/approve", headers=second).status_code == 200
        assert client.post("/ops/releases/candidate-v2/activate", headers=first).status_code == 200
        assert client.get("/ui/demo").json()["model_version"] == "candidate-v2"
        sample = json.loads((BUNDLE / "example_request.json").read_text())
        assert (
            client.post("/v1/predict", headers=first, json=sample).json()["model_version"]
            == "candidate-v2"
        )
    with TestClient(create_app(BUNDLE, KEY, state)) as client:
        headers = auth(client, "approver")
        assert client.get("/ops/releases", headers=headers).json()["active"] == "candidate-v2"
        assert client.post("/ops/rollback", headers=headers).json()["active"] == baseline
        assert client.get("/ui/demo").json()["model_version"] == baseline


def test_failed_gates_and_changed_files_cannot_be_promoted(tmp_path):
    state = tmp_path / "state"
    store = Store(state)
    add_user(store, "submitter", PASSWORD, "admin")
    add_user(store, "approver", PASSWORD, "admin")
    with TestClient(create_app(BUNDLE, KEY, state)) as client:
        headers = auth(client, "approver")
        result = stage(store, candidate(tmp_path, "bad-release", True), "submitter")
        assert not result["passed"]
        assert client.post("/ops/releases/bad-release/approve", headers=headers).status_code == 409
        stage(store, candidate(tmp_path), "submitter")
        assert client.post("/ops/releases/candidate-v2/approve", headers=headers).status_code == 200
        path = state / "releases/candidate-v2/manifest.json"
        path.write_text(path.read_text() + " ")
        assert (
            client.post("/ops/releases/candidate-v2/activate", headers=headers).status_code == 409
        )
        assert client.get("/health/ready").status_code == 200


def test_corrupt_active_release_disables_readiness_on_restart(tmp_path):
    state = tmp_path / "state"
    with TestClient(create_app(BUNDLE, KEY, state)) as client:
        version = client.get("/ui/demo").json()["model_version"]
    path = state / "releases" / version / "model.joblib"
    with path.open("ab") as stream:
        stream.write(b"corrupt")
    with TestClient(create_app(BUNDLE, KEY, state)) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/ui/demo").status_code == 503


def test_non_admin_cannot_stage(tmp_path):
    store = Store(tmp_path / "state")
    add_user(store, "analyst", PASSWORD, "analyst")
    with pytest.raises(ValueError, match="administrator"):
        stage(store, BUNDLE, "analyst")


def test_recover_corrupt_active_model_with_previous_release(tmp_path):
    state = tmp_path / "state"
    store = Store(state)
    add_user(store, "submitter", PASSWORD, "admin")
    add_user(store, "approver", PASSWORD, "admin")
    with TestClient(create_app(BUNDLE, KEY, state)) as client:
        headers = auth(client, "approver")
        original = client.get("/ui/demo").json()["model_version"]
        stage(store, candidate(tmp_path), "submitter")
        assert client.post("/ops/releases/candidate-v2/approve", headers=headers).status_code == 200
        assert (
            client.post("/ops/releases/candidate-v2/activate", headers=headers).status_code == 200
        )
    with (state / "releases/candidate-v2/model.joblib").open("ab") as stream:
        stream.write(b"tampered")
    with TestClient(create_app(BUNDLE, KEY, state)) as client:
        assert client.get("/health/ready").status_code == 503
        headers = auth(client, "approver")
        assert client.post("/ops/rollback", headers=headers).json()["active"] == original
        assert client.get("/health/ready").status_code == 200

from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.linear_model import LogisticRegression

from fraudguard.api import create_app
from fraudguard.behavioral import FEATURES, Event, offline_features
from fraudguard.behavioral_serving import load_bundle, save_bundle

KEY = "test-secret-long-enough"


@pytest.fixture
def behavioral_client(bundle, tmp_path, monkeypatch):
    path = tmp_path / "behavioral"
    frame = pd.DataFrame(
        np.random.default_rng(42).normal(size=(50, len(FEATURES))), columns=FEATURES
    )
    model = LogisticRegression().fit(frame, np.arange(50) % 2)
    save_bundle(
        path,
        model,
        {
            "threshold": 0.5,
            "dataset": "test",
            "dataset_sha256": "test",
            "seed": 42,
            "selected_model": "logistic",
        },
        reference={"log_amount": {"inner_edges": [1.0], "counts": [20, 30]}},
    )
    monkeypatch.setenv("BEHAVIORAL_MODEL_DIR", str(path))
    app = create_app(bundle[0], KEY, tmp_path / "state")
    with TestClient(app) as client:
        yield client, app, model


def request_data():
    history = [
        Event("a", "account", "shop", 100, 10, 51, 0),
        Event("b", "account", "shop", 700, 20, 51, 0),
    ]
    current = Event("c", "account", "other", 701, 80, 52, 1)
    return {"transaction": asdict(current)}, history + [current]


def test_authenticated_behavioral_endpoint_matches_offline(behavioral_client):
    client, _, model = behavioral_client
    payload, events = request_data()
    assert client.post("/v1/behavioral/predict", json=payload).status_code == 401
    for event in events[:-1]:
        assert (
            client.post(
                "/v1/behavioral/predict",
                json={"transaction": asdict(event)},
                headers={"X-API-Key": KEY},
            ).status_code
            == 200
        )
    response = client.post("/v1/behavioral/predict", json=payload, headers={"X-API-Key": KEY})
    assert response.status_code == 200
    expected = offline_features(events).iloc[[-1]]
    assert response.json()["features"] == expected.iloc[0].to_dict()
    assert response.json()["risk_score"] == pytest.approx(model.predict_proba(expected)[0, 1])
    assert response.json()["score_kind"] == "uncalibrated"
    assert (
        "fraudguard_behavioral_predictions_total{decision="
        in client.get("/metrics", headers={"X-API-Key": KEY}).text
    )


@pytest.mark.parametrize("extra", ["history", "label", "timestamp"])
def test_behavioral_rejects_client_history_and_labels(behavioral_client, extra):
    client, _, _ = behavioral_client
    payload, _ = request_data()
    if extra == "history":
        payload["history"] = []
    elif extra == "timestamp":
        payload["transaction"]["timestamp"] = 10**100
    else:
        payload["transaction"]["label"] = 1
    assert (
        client.post("/v1/behavioral/predict", json=payload, headers={"X-API-Key": KEY}).status_code
        == 422
    )


def test_busy_rejection_does_not_debit_quota(behavioral_client, payload):
    client, app, _ = behavioral_client
    slots = app.state.inference_slots
    for _ in range(4):
        assert slots.acquire(blocking=False)
    try:
        for route, data in [
            ("/v1/predict", payload),
            ("/v1/behavioral/predict", request_data()[0]),
        ]:
            assert client.post(route, json=data, headers={"X-API-Key": KEY}).status_code == 503
        assert app.state.store.query("SELECT * FROM quotas") == []
    finally:
        for _ in range(4):
            slots.release()


def test_corrupted_behavioral_bundle_is_rejected(tmp_path):
    (tmp_path / "model.joblib").write_bytes(b"not a model")
    (tmp_path / "manifest.json").write_text(
        '{"schema":"behavioral-v1","features":[],"model_sha256":"wrong"}'
    )
    with pytest.raises(ValueError, match="Invalid"):
        load_bundle(tmp_path)


def test_persisted_retries_conflicts_restart_and_cold_start(behavioral_client):
    from fraudguard.behavioral_serving import BehavioralRequest
    from fraudguard.behavioral_store import ingest
    from fraudguard.store import Store

    client, app, _ = behavioral_client
    payload, events = request_data()
    headers = {"X-API-Key": KEY}
    first = {"transaction": asdict(events[0])}
    result = client.post("/v1/behavioral/predict", json=first, headers=headers).json()
    assert result["decision"] == "review"
    assert result["decision_reason"] == "cold_start_manual_review"
    assert client.post("/v1/behavioral/predict", json=first, headers=headers).json() == result
    changed = {"transaction": {**first["transaction"], "amount": 999.0}}
    assert client.post("/v1/behavioral/predict", json=changed, headers=headers).status_code == 422
    owner = app.state.store.query("SELECT owner FROM predictions")[0]["owner"]
    restarted = Store(app.state.store.directory)
    second = ingest(
        restarted, app.state.behavioral, owner, BehavioralRequest(transaction=asdict(events[1]))
    )
    assert second["features"]["history_count_30d"] == 1
    third = client.post("/v1/behavioral/predict", json=payload, headers=headers).json()
    assert third["features"] == offline_features(events).iloc[-1].to_dict()
    late = {"transaction": {**first["transaction"], "event_id": "late"}}
    assert client.post("/v1/behavioral/predict", json=late, headers=headers).status_code == 422
    assert len(restarted.query("SELECT * FROM predictions")) == 3
    assert len(restarted.query("SELECT * FROM behavioral_events")) == 3


def test_atomic_failure_and_concurrent_duplicate(behavioral_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    import fraudguard.behavioral_store as module
    from fraudguard.behavioral_serving import BehavioralRequest
    from fraudguard.behavioral_store import ingest

    _, app, _ = behavioral_client
    payload = BehavioralRequest(**request_data()[0])
    original = module.score_event

    def fail(*args):
        raise RuntimeError("injected scoring failure")

    monkeypatch.setattr(module, "score_event", fail)
    with pytest.raises(RuntimeError, match="injected"):
        ingest(app.state.store, app.state.behavioral, "owner", payload)
    for table in ("predictions", "behavioral_events", "behavioral_accounts"):
        assert app.state.store.query("SELECT * FROM " + table) == []
    monkeypatch.setattr(module, "score_event", original)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: ingest(app.state.store, app.state.behavioral, "owner", payload), range(4)
            )
        )
    assert all(r == results[0] for r in results)
    assert len(app.state.store.query("SELECT * FROM predictions")) == 1


def test_review_labels_isolation_and_monitoring(behavioral_client):
    from fraudguard.auth import add_user

    client, app, _ = behavioral_client
    tokens = {}
    for name, role in [("first", "analyst"), ("second", "analyst"), ("admin", "admin")]:
        add_user(app.state.store, name, "long-test-password", role)
        login = client.post(
            "/auth/login", json={"username": name, "password": "long-test-password"}
        )
        tokens[name] = {"Authorization": "Bearer " + login.json()["access_token"]}
    payload, events = request_data()
    for event in events[:-1]:
        assert (
            client.post(
                "/v1/behavioral/predict",
                json={"transaction": asdict(event)},
                headers=tokens["first"],
            ).status_code
            == 200
        )
    result = client.post("/v1/behavioral/predict", json=payload, headers=tokens["first"]).json()
    isolated = client.post("/v1/behavioral/predict", json=payload, headers=tokens["second"]).json()
    assert isolated["cold_start"] and not result["cold_start"]
    identifier = result["prediction_id"]
    route = f"/ops/predictions/{identifier}"
    assert client.get(route, headers=tokens["second"]).status_code == 404
    detail = client.get(route, headers=tokens["first"]).json()
    assert detail["behavioral"] == result
    assert (
        client.post(
            route + "/review",
            headers=tokens["first"],
            json={"disposition": "cleared", "note": "Verified evidence"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            route + "/label",
            headers=tokens["first"],
            json={"fraud": 0, "source": "confirmed outcome"},
        ).status_code
        == 200
    )
    monitor = client.get("/ops/monitoring", headers=tokens["admin"]).json()["behavioral"]
    assert monitor["quality"]["predictions"] == 4
    assert monitor["quality"]["labeled"] == 1
    assert monitor["drift"]["rows"] == 4


def test_backup_preserves_events_and_model(behavioral_client, tmp_path):
    import json
    from io import BytesIO

    import joblib

    from fraudguard.backups import create_backup, unpack
    from fraudguard.behavioral_serving import BehavioralRequest
    from fraudguard.behavioral_store import ingest
    from fraudguard.store import Store

    client, app, _ = behavioral_client
    payload, events = request_data()
    first = {"transaction": asdict(events[0])}
    client.post("/v1/behavioral/predict", json=first, headers={"X-API-Key": KEY})
    backup = create_backup(app.state.store, tmp_path / "backups")
    restored = tmp_path / "restored"
    restored.mkdir()
    unpack(tmp_path / "backups" / backup["file"], restored)
    store = Store(restored)
    saved = store.query("SELECT * FROM behavioral_models")[0]
    bundle = (joblib.load(BytesIO(saved["artifact"])), json.loads(saved["manifest"]))
    owner = store.query("SELECT owner FROM predictions")[0]["owner"]
    response = ingest(store, bundle, owner, BehavioralRequest(**payload))
    assert response["features"] == offline_features([events[0], events[-1]]).iloc[-1].to_dict()


def test_equal_time_boundary_and_retention(behavioral_client):
    from fraudguard.behavioral import WINDOW

    client, app, _ = behavioral_client
    headers = {"X-API-Key": KEY}
    events = [
        Event("a", "window", "shop", 100, 10, 51, 0),
        Event("b", "window", "shop", 100, 20, 51, 0),
        Event("c", "window", "shop", 100 + WINDOW, 30, 51, 0),
        Event("d", "window", "shop", 101 + WINDOW, 40, 51, 0),
    ]
    expected = offline_features(events)
    for i, event in enumerate(events):
        response = client.post(
            "/v1/behavioral/predict", headers=headers, json={"transaction": asdict(event)}
        )
        assert response.status_code == 200
        assert response.json()["features"] == expected.iloc[i].to_dict()
    with pytest.raises(ValueError, match="30 days"):
        app.state.store.prune(7)

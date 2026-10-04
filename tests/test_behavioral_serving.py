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
    return {"transaction": asdict(current), "history": [asdict(e) for e in history]}, history + [
        current
    ]


def test_authenticated_behavioral_endpoint_matches_offline(behavioral_client):
    client, _, model = behavioral_client
    payload, events = request_data()
    assert client.post("/v1/behavioral/predict", json=payload).status_code == 401
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


@pytest.mark.parametrize("change", ["future", "account", "duplicate", "label"])
def test_behavioral_rejects_invalid_history(behavioral_client, change):
    client, _, _ = behavioral_client
    payload, _ = request_data()
    if change == "future":
        payload["history"][0]["timestamp"] = 701
    elif change == "account":
        payload["history"][0]["account_id"] = "another-account"
    elif change == "duplicate":
        payload["history"][0]["event_id"] = "c"
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

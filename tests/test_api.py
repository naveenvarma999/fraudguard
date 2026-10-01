import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fraudguard.api import create_app
from fraudguard.artifacts import load_bundle
from fraudguard.data import FEATURES

KEY = "test-secret-long-enough"


@pytest.fixture
def client(bundle):
    with TestClient(create_app(bundle[0], KEY)) as client:
        yield client


def test_auth_health_and_offline_online_parity(client, bundle, payload):
    assert client.get("/health/ready").status_code == 200
    assert client.post("/v1/predict", json=payload).status_code == 401
    response = client.post("/v1/predict", json=payload, headers={"X-API-Key": KEY})
    assert response.status_code == 200
    result = response.json()
    frame = pd.DataFrame(payload["transactions"])[FEATURES]
    np.testing.assert_allclose(
        result["predictions"][0]["fraud_probability"], bundle[1].predict_proba(frame)[0, 1]
    )
    assert result["model_version"] == "test-v1"
    metrics = client.get("/metrics", headers={"X-API-Key": KEY})
    assert "fraudguard_predictions_total" in metrics.text
    assert "test-001" not in metrics.text
    assert client.get("/metrics").status_code == 401


@pytest.mark.parametrize(
    "change", ["missing", "extra", "negative", "string", "duplicate", "empty", "oversized"]
)
def test_invalid_requests(client, payload, change):
    row = payload["transactions"][0]
    if change == "missing":
        del row["V1"]
    elif change == "extra":
        row["Class"] = 1
    elif change == "negative":
        row["Amount"] = -1
    elif change == "string":
        row["V1"] = "raw-private-input"
    elif change == "duplicate":
        payload["transactions"].append(row.copy())
    elif change == "empty":
        payload["transactions"] = []
    else:
        payload["transactions"] = [dict(row, transaction_id=f"id-{i}") for i in range(101)]
    response = client.post("/v1/predict", json=payload, headers={"X-API-Key": KEY})
    assert response.status_code == 422
    assert "raw-private-input" not in response.text


def test_nan_and_oversize_body(client, payload):
    payload["transactions"][0]["V1"] = float("nan")
    response = client.post("/v1/predict", content=json.dumps(payload), headers={"X-API-Key": KEY})
    assert response.status_code == 422
    response = client.post("/v1/predict", content=b"x" * 262145, headers={"X-API-Key": KEY})
    assert response.status_code == 413


def test_missing_model_is_alive_but_not_ready(tmp_path):
    with TestClient(create_app(tmp_path / "missing", KEY)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_short_key_fails_startup(bundle):
    with pytest.raises(RuntimeError, match="API_KEY"):
        with TestClient(create_app(bundle[0], "short")):
            pass


@pytest.mark.parametrize("change", ["checksum", "schema", "version"])
def test_artifact_tampering_rejected(bundle, change):
    path, _ = bundle
    if change == "checksum":
        with (path / "model.joblib").open("ab") as stream:
            stream.write(b"tampered")
    else:
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["schema_version" if change == "schema" else "sklearn_version"] = "invalid"
        (path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_bundle(path)
    with TestClient(create_app(path, KEY)) as client:
        assert client.get("/health/ready").status_code == 503

from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from fraudguard.api import create_app
from fraudguard.data import FEATURES


def test_public_dashboard_and_fixed_demo(bundle):
    key = "dashboard-secret-test-only"
    with TestClient(create_app(bundle[0], key)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Transaction review" in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        for name in ("app.mjs", "data.mjs", "style.css", "sample.csv", "sample.json"):
            asset = client.get(f"/assets/{name}")
            assert asset.status_code == 200
            assert key not in asset.text
        demo = client.get("/ui/demo")
        assert demo.headers["cache-control"] == "no-store"
        data = demo.json()
        assert len(data["transactions"]) == 8
        np.testing.assert_allclose(
            [p["fraud_probability"] for p in data["predictions"]],
            bundle[1].predict_proba(pd.DataFrame(data["transactions"])[FEATURES])[:, 1],
        )
        payload = {"transactions": data["transactions"]}
        assert client.post("/v1/predict", json=payload).status_code == 401
        scored = client.post("/v1/predict", json=payload, headers={"X-API-Key": key}).json()
        assert scored["predictions"] == data["predictions"]
        assert client.get("/ui/benchmark").status_code == 404


def test_demo_missing_model(tmp_path):
    with TestClient(create_app(tmp_path / "missing", "dashboard-secret-test-only")) as client:
        assert client.get("/").status_code == 200
        assert client.get("/ui/demo").status_code == 503


def test_benchmark_matches_bundled_model():
    release = Path(__file__).resolve().parents[1] / "artifacts/benchmark"
    with TestClient(create_app(release, "dashboard-secret-test-only")) as client:
        response = client.get("/ui/benchmark")
        assert response.status_code == 200
        assert response.json()["test"]["rows"] == 42438
        assert response.json()["test"]["confusion"]["tp"] == 40

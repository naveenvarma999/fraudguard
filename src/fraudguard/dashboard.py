"""Public dashboard assets and fixed benchmark demo; private inputs use the API."""

import json
from pathlib import Path

import pandas as pd
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fraudguard.data import FEATURES

ASSETS = Path(__file__).parent / "static"


def prepare_demo(model, manifest):
    records = json.loads((ASSETS / "sample.json").read_text(encoding="utf-8"))["transactions"]
    scores = model.predict_proba(pd.DataFrame(records)[FEATURES])[:, 1]
    threshold = manifest["policy"]["threshold"]
    return {
        "model_version": manifest["run_id"],
        "schema_version": manifest["schema_version"],
        "threshold": threshold,
        "source": "Curated public benchmark examples; not a representative traffic sample",
        "transactions": records,
        "predictions": [
            {
                "transaction_id": row["transaction_id"],
                "fraud_probability": float(p),
                "decision": "review" if p >= threshold else "pass",
            }
            for row, p in zip(records, scores)
        ],
    }


def mount_dashboard(app, release):
    app.mount("/assets", StaticFiles(directory=ASSETS), name="dashboard-assets")

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(ASSETS / "index.html", media_type="text/html")

    @app.get("/ui/demo", include_in_schema=False)
    def demo():
        if app.state.model is None:
            raise HTTPException(status_code=503, detail="Model unavailable")
        if app.state.demo is None:
            raise HTTPException(status_code=503, detail="Demo unavailable")
        return app.state.demo

    @app.get("/ui/benchmark", include_in_schema=False)
    def benchmark():
        path = app.state.runtime.path / "evaluation.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Benchmark report unavailable")
        report = json.loads(path.read_text(encoding="utf-8"))
        if app.state.model is None or report["dataset"]["sha256"] != app.state.manifest.get(
            "dataset_sha256"
        ):
            raise HTTPException(status_code=503, detail="Matching benchmark unavailable")
        return {
            "champion": report["champion"],
            "test": report["test"],
            "splits": report["splits"],
            "created_utc": report["created_utc"],
            "model_version": app.state.manifest["run_id"],
            "policy": report["policy"],
            "candidates": [
                {"name": trial["name"], "ap": trial["tune_average_precision"]}
                for trial in report["experiments"]
            ],
        }

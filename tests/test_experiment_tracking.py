import json

import pytest

from fraudguard.experiment_tracking import track_report


def test_mlflow_records_metrics_and_report(tmp_path, monkeypatch):
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.chdir(tmp_path)
    uri = "sqlite:///" + (tmp_path / "mlflow.db").as_posix()
    report = {
        "seed": 42,
        "accounts": 100,
        "days": 60,
        "rows": 10000,
        "dataset_sha256": "test",
        "selected_model": "logistic",
        "threshold": 0.5,
        "evaluation": {"test_unseen": {"average_precision": 0.7, "rows": 100}},
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    run_id = track_report(path, uri)
    client = mlflow.MlflowClient(tracking_uri=uri)
    result = client.get_run(run_id)
    assert result.info.status == "FINISHED"
    assert result.data.metrics["test_unseen.average_precision"] == 0.7
    assert result.data.tags["dataset.kind"] == "synthetic"
    assert client.list_artifacts(run_id)[0].path == "report.json"

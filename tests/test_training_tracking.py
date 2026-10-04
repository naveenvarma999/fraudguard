import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from fraudguard.experiment_tracking import TrainingRun


def test_training_tracks_fitted_candidate_and_failed_run(tmp_path, monkeypatch):
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.chdir(tmp_path)
    uri = "sqlite:///" + (tmp_path / "training.db").as_posix()
    model = LogisticRegression()
    frame = pd.DataFrame({"amount": [1, 2, 3, 4]})
    with TrainingRun(uri) as run:
        with run.candidate("logistic", model, frame.columns) as metric:
            model.fit(frame, [0, 0, 1, 1])
            metric(0.8)
        parent_id = run.run_id
    client = mlflow.MlflowClient(tracking_uri=uri)
    children = client.search_runs(
        [run.experiment_id], filter_string=f"tags.`mlflow.parentRunId` = '{parent_id}'"
    )
    assert len(children) == 1
    child = children[0]
    assert child.info.status == "FINISHED"
    assert child.data.metrics["selection.average_precision"] == 0.8
    assert {a.path for a in client.list_artifacts(child.info.run_id, "model")} == {
        "model/model.joblib",
        "model/features.json",
    }
    with pytest.raises(RuntimeError):
        with TrainingRun(uri) as failed:
            with failed.candidate("broken", model, frame.columns):
                raise RuntimeError("training failed")
    assert client.get_run(failed.run_id).info.status == "FAILED"
    children = client.search_runs(
        [failed.experiment_id], filter_string=f"tags.`mlflow.parentRunId` = '{failed.run_id}'"
    )
    assert children[0].info.status == "FAILED"

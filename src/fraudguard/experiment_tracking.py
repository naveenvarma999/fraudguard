"""Optional MLflow evidence tracking, outside the serving dependency set."""

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path


class TrainingRun:
    """Create runs before fitting; log each fitted candidate and failures."""

    def __init__(self, uri=None):
        self.uri, self.client, self.run_id = uri, None, None

    def __enter__(self):
        if self.uri:
            from mlflow import MlflowClient

            self.client = MlflowClient(tracking_uri=self.uri)
            name = "fraudguard-training"
            experiment = self.client.get_experiment_by_name(name)
            eid = experiment.experiment_id if experiment else self.client.create_experiment(name)
            self.experiment_id = eid
            self.run_id = self.client.create_run(
                eid, tags={"phase": "training", "dataset.kind": "synthetic"}
            ).info.run_id
        return self

    @contextmanager
    def candidate(self, name, model, features):
        if not self.client:
            yield lambda value: None
            return
        child = self.client.create_run(
            self.experiment_id, tags={"mlflow.parentRunId": self.run_id, "mlflow.runName": name}
        ).info.run_id
        try:
            for key, value in model.get_params(deep=True).items():
                if value is None or isinstance(value, (str, int, float, bool)):
                    self.client.log_param(child, key, str(value))
            yield lambda value: self.client.log_metric(child, "selection.average_precision", value)
            import joblib

            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary)
                joblib.dump(model, path / "model.joblib")
                (path / "features.json").write_text(json.dumps(list(features)), encoding="utf-8")
                self.client.log_artifacts(child, str(path), "model")
            self.client.set_terminated(child, "FINISHED")
        except Exception:
            self.client.set_terminated(child, "FAILED")
            raise

    def parameters(self, values):
        if self.client:
            for name, value in values.items():
                self.client.log_param(self.run_id, name, value)

    def finish(self, report, output):
        if self.client:
            for key in ("seed", "dataset", "dataset_sha256", "rows", "selected_model", "threshold"):
                self.client.log_param(self.run_id, key, report[key])
            for cohort, metrics in report["evaluation"].items():
                for name, value in metrics.items():
                    if isinstance(value, (int, float)):
                        self.client.log_metric(self.run_id, f"{cohort}.{name}", value)
            self.client.log_artifact(self.run_id, str(Path(output) / "report.json"))

    def __exit__(self, kind, value, traceback):
        if self.client:
            self.client.set_terminated(self.run_id, "FAILED" if kind else "FINISHED")


def track_report(report_path, tracking_uri):
    from mlflow import MlflowClient

    path = Path(report_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    client = MlflowClient(tracking_uri=tracking_uri)
    name = "fraudguard-behavioral-research"
    experiment = client.get_experiment_by_name(name)
    experiment_id = experiment.experiment_id if experiment else client.create_experiment(name)
    run = client.create_run(
        experiment_id, tags={"dataset.kind": "synthetic", "deployment": "research-only"}
    )
    run_id = run.info.run_id
    try:
        for key in (
            "seed",
            "accounts",
            "days",
            "rows",
            "dataset_sha256",
            "selected_model",
            "threshold",
        ):
            client.log_param(run_id, key, report[key])
        for name, result in report["evaluation"].items():
            for metric, value in result.items():
                if isinstance(value, (int, float)):
                    client.log_metric(run_id, f"{name}.{metric}", value)
        client.log_artifact(run_id, str(path))
        client.set_terminated(run_id, "FINISHED")
    except Exception:
        client.set_terminated(run_id, "FAILED")
        raise
    return run_id


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Log an existing behavioral report to MLflow")
    parser.add_argument("--report", required=True)
    parser.add_argument("--tracking-uri", required=True)
    args = parser.parse_args()
    print(track_report(args.report, args.tracking_uri))

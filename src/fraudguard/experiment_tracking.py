"""Optional MLflow evidence tracking, outside the serving dependency set."""

import json
from pathlib import Path


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
                if value is not None:
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

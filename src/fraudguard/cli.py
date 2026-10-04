"""Command line entry points for data, training, scoring and monitoring."""

import argparse
import json
from pathlib import Path

import pandas as pd
from threadpoolctl import threadpool_limits

from fraudguard.artifacts import load_bundle
from fraudguard.data import FEATURES, download, validate
from fraudguard.evaluation import metrics
from fraudguard.monitoring import drift_report


def main():
    parser = argparse.ArgumentParser(prog="fraudguard")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("download", help="Download the public benchmark")
    fetch.add_argument("--output", required=True)
    fit = commands.add_parser("train", help="Train and evaluate on disjoint time windows")
    fit.add_argument("--data", required=True)
    fit.add_argument("--output", required=True)
    fit.add_argument("--seed", type=int, default=42)
    fit.add_argument("--max-review-rate", type=float, default=0.01)
    fit.add_argument("--review-cost", type=float, default=2.0)
    fit.add_argument("--missed-cost", type=float, default=100.0)
    behavioral = commands.add_parser(
        "behavioral-experiment", help="Synthetic point-in-time feature experiment (research only)"
    )
    behavioral.add_argument("--output", required=True)
    behavioral.add_argument("--seed", type=int, default=42)
    behavioral.add_argument("--accounts", type=int, default=100)
    behavioral.add_argument("--days", type=int, default=60)
    for name in ("score", "monitor", "evaluate"):
        sub = commands.add_parser(name)
        sub.add_argument("--data", required=True)
        sub.add_argument("--model-dir", required=True)
        sub.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "behavioral-experiment":
        from fraudguard.behavioral_experiment import run

        report = run(args.output, args.seed, args.accounts, args.days)
        print(json.dumps(report, indent=2))
    elif args.command == "download":
        print(json.dumps(download(args.output), indent=2))
    elif args.command == "train":
        from fraudguard.training import train

        report = train(
            args.data,
            args.output,
            args.seed,
            args.max_review_rate,
            args.review_cost,
            args.missed_cost,
        )
        print(json.dumps(report["test"], indent=2))
    else:
        model, manifest = load_bundle(args.model_dir)
        frame = validate(pd.read_csv(args.data))
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if args.command == "monitor":
            result = drift_report(manifest["reference"], frame)
        else:
            with threadpool_limits(limits=2):
                p = model.predict_proba(frame[FEATURES])[:, 1]
            policy = manifest["policy"]
            if args.command == "score":
                pd.DataFrame(
                    {
                        "row_index": frame.index,
                        "fraud_probability": p,
                        "review": p >= policy["threshold"],
                        "model_version": manifest["run_id"],
                    }
                ).to_csv(output, index=False)
                return
            if (
                "Class" not in frame
                or not frame.Class.isin([0, 1]).all()
                or frame.Class.nunique() != 2
            ):
                raise ValueError("Evaluation needs Class labels with both 0 and 1")
            result = metrics(
                frame.Class,
                p,
                policy["threshold"],
                policy["review_cost"],
                policy["missed_fraud_cost"],
            )
            result["model_version"] = manifest["run_id"]
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

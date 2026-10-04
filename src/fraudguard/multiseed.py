"""Fixed, predeclared seeds; publish every run and mean/range, never the best run."""

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

from fraudguard.behavioral_experiment import run

SEEDS = [42, *range(10)]


def summarize(reports):
    summary = {
        "seeds": [r["seed"] for r in reports],
        "runs": len(reports),
        "selected_model_counts": dict(Counter(r["selected_model"] for r in reports)),
        "cohorts": {},
    }
    for cohort in reports[0]["evaluation"]:
        metrics = {}
        for metric in ("average_precision", "precision", "recall", "review_rate", "fraud_rate"):
            values = [
                r["evaluation"][cohort][metric]
                for r in reports
                if r["evaluation"][cohort][metric] is not None
            ]
            metrics[metric] = {
                "valid_runs": len(values),
                "mean": float(np.mean(values)) if values else None,
                "min": float(min(values)) if values else None,
                "max": float(max(values)) if values else None,
                "std": float(np.std(values, ddof=1)) if len(values) > 1 else None,
            }
        summary["cohorts"][cohort] = metrics
    differences = [
        r["selection_average_precision"]["histogram_gbm"]
        - r["selection_average_precision"]["logistic"]
        for r in reports
    ]
    summary["selection_gbm_minus_logistic_ap"] = {
        "mean": float(np.mean(differences)),
        "min": min(differences),
        "max": max(differences),
    }
    summary["interpretation"] = (
        "Variation across synthetic datasets and fitting seeds; not a confidence interval on bank performance. Undefined cohorts are counted, not converted to zero."
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    reports = []
    for seed in SEEDS:
        print(f"Training seed {seed}", flush=True)
        reports.append(run(output / f"seed-{seed}", seed=seed))
    (output / "summary.json").write_text(
        json.dumps(summarize(reports), indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )

"""Compare critical experiment evidence; fail CI on unreviewed result changes."""

import argparse
import json
import math
from pathlib import Path

FIELDS = (
    "dataset",
    "seed",
    "accounts",
    "days",
    "rows",
    "dataset_sha256",
    "exact_online_offline_parity",
    "feature_names",
    "split_rows",
    "cutoffs",
    "selection_average_precision",
    "selected_model",
    "selection_review_budget",
    "threshold",
    "evaluation",
    "fraud_rate",
)


def compare(expected, actual):
    def check(left, right, path):
        if isinstance(left, dict):
            if not isinstance(right, dict) or left.keys() != right.keys():
                raise ValueError(f"Reproduction key mismatch at {path}")
            for name in left:
                check(left[name], right[name], f"{path}.{name}")
        elif isinstance(left, float):
            if not isinstance(right, (int, float)) or not math.isclose(
                left, right, rel_tol=1e-7, abs_tol=1e-9
            ):
                raise ValueError(f"Reproduction numerical mismatch at {path}")
        elif left != right:
            raise ValueError(f"Reproduction mismatch at {path}")

    for name in FIELDS:
        check(expected[name], actual[name], name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--actual", required=True)
    args = parser.parse_args()
    compare(
        *(json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.expected, args.actual))
    )
    print(
        "Dataset, feature contract, splits, selection and evaluation reproduce within declared tolerances."
    )

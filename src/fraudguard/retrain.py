"""Train a candidate from arrived labels and immutable features saved at scoring time."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.frozen import FrozenEstimator
from threadpoolctl import threadpool_limits

from fraudguard.behavioral_serving import save_bundle
from fraudguard.enriched import ENRICHED
from fraudguard.ml_study import metrics


def run(store, output, asof=None):
    asof = time.time() if asof is None else asof
    # Only outcomes known by the snapshot cutoff; later corrections excluded.
    rows = store.query(
        """SELECT p.at,e.response,h.fraud,h.available_at FROM account_events e
      JOIN predictions p ON p.id=e.prediction_id JOIN label_history h ON h.prediction_id=p.id
      WHERE h.available_at<=? AND p.at<=? AND NOT EXISTS
      (SELECT 1 FROM label_history n WHERE n.prediction_id=h.prediction_id AND n.available_at<=?
       AND (n.available_at>h.available_at OR (n.available_at=h.available_at AND n.id>h.id))) ORDER BY p.at,p.id""",
        (asof, asof, asof),
    )
    for row in rows:
        response = json.loads(row["response"])
        row["snapshot"] = response.get("training_features", response["features"])
    rows = [r for r in rows if set(ENRICHED) <= set(r["snapshot"])]
    if len(rows) < 500:
        raise ValueError("At least 500 arrived labels on enriched predictions are required")
    x = pd.DataFrame([r["snapshot"] for r in rows])[ENRICHED]
    y = np.array([r["fraud"] for r in rows])
    n = len(rows)
    a, b, c = [int(n * p) for p in (0.5, 0.65, 0.8)]
    # Refuse boundaries that split equal received-time peers.
    for name, boundary in [("fit", a), ("calibration", b), ("policy", c)]:
        if rows[boundary - 1]["at"] == rows[boundary]["at"]:
            raise ValueError(f"{name} boundary has equal-time peers; collect a larger snapshot")
    slices = [slice(0, a), slice(a, b), slice(b, c), slice(c, n)]
    available = np.array([r["available_at"] for r in rows])
    indices = [
        np.arange(n)[s][available[s] < cutoff]
        for s, cutoff in zip(slices, [rows[a]["at"], rows[b]["at"], rows[c]["at"], asof + 1e-6])
    ]
    fit, calibration, policy, test = indices
    if any(np.sum(y[s]) < 5 or np.sum(1 - y[s]) < 5 for s in indices):
        raise ValueError(
            "Every chronological partition needs five fraud and five non-fraud outcomes"
        )
    model = HistGradientBoostingClassifier(
        max_iter=120, max_leaf_nodes=15, l2_regularization=2, random_state=42
    )
    with threadpool_limits(limits=2):
        model.fit(x.iloc[fit], y[fit])
        calibrated = CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid")
        calibrated.fit(x.iloc[calibration], y[calibration])
        threshold = float(np.quantile(calibrated.predict_proba(x.iloc[policy])[:, 1], 0.95))
        evaluation = metrics(y[test], calibrated.predict_proba(x.iloc[test])[:, 1], threshold)
    report = {
        "threshold": threshold,
        "dataset": "Arrived workspace labels; saved point-in-time features",
        "dataset_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
        "seed": 42,
        "selected_model": "histogram_gbm",
        "score_kind": "sigmoid_calibrated_observed",
        "training_asof": asof,
        "rows": n,
        "evaluation": evaluation,
        "split_rows": [len(s) for s in indices],
    }
    reference = {}
    for feature in ENRICHED:
        values = x.iloc[fit][feature].to_numpy()
        edges = np.unique(np.quantile(values, np.linspace(0.1, 0.9, 9)))
        reference[feature] = {
            "inner_edges": edges.tolist(),
            "counts": np.histogram(values, [-np.inf, *edges, np.inf])[0].tolist(),
        }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    save_bundle(output / "model", calibrated, report, reference)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    from fraudguard.store import Store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(run(Store(args.state_dir), args.output), indent=2))

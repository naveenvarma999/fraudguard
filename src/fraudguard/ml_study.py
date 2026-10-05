"""Sparkov ablation, chronological calibration and account-cluster uncertainty."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from fraudguard.behavioral import FEATURES, Event, offline_features, replay_features
from fraudguard.behavioral_serving import save_bundle
from fraudguard.enriched import BASE, CONTEXT, ENRICHED, context, delayed_features


def metrics(y, scores, threshold, weights=None):
    y, scores = np.asarray(y), np.asarray(scores)
    w = np.ones(len(y)) if weights is None else weights
    positives, negatives = np.sum(w * y), np.sum(w * (1 - y))
    if positives == 0 or negatives == 0:
        return {"status": "insufficient_data"}
    ap = float(average_precision_score(y, scores, sample_weight=w))
    prevalence = float(positives / w.sum())
    return {
        "status": "measured",
        "rows": len(y),
        "fraud_cases": int(np.sum(y)),
        "fraud_rate": prevalence,
        "average_precision": ap,
        "ap_lift": ap / prevalence,
        "recall": float(np.sum(w * y * (scores >= threshold)) / positives),
        "false_positive_rate": float(np.sum(w * (1 - y) * (scores >= threshold)) / negatives),
        "review_rate": float(np.sum(w * (scores >= threshold)) / w.sum()),
        "brier": float(brier_score_loss(y, scores, sample_weight=w)),
    }


def cluster_intervals(y, scores, accounts, threshold, repeats=200, seed=42):
    names, inverse = np.unique(accounts, return_inverse=True)
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(repeats):
        counts = np.bincount(rng.integers(0, len(names), len(names)), minlength=len(names))
        result = metrics(y, scores, threshold, counts[inverse])
        if result["status"] == "measured":
            values.append(result)
    return {
        "unit": "account",
        "method": "percentile cluster bootstrap",
        "confidence": 0.95,
        "requested": repeats,
        "valid": len(values),
        "accounts": len(names),
        "intervals": {
            k: np.quantile([v[k] for v in values], [0.025, 0.975]).tolist()
            for k in ("average_precision", "ap_lift", "recall", "brier")
        }
        if values
        else {},
    }


def train(data, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    frame = pd.read_csv(data).sort_values(["timestamp", "event_id"]).reset_index(drop=True)
    if frame.event_id.duplicated().any() or not (frame.label_available_at > frame.timestamp).all():
        raise ValueError("Unique events and strictly delayed labels required")
    if frame.account_id.nunique() < 1000:
        raise ValueError("This study requires at least 1,000 accounts")
    held = frame.account_id.map(
        lambda a: int(hashlib.sha256(a.encode()).hexdigest()[:8], 16) % 5 == 0
    )
    start, end = frame.timestamp.min(), frame.timestamp.max() + 1
    cutoffs = [int(start + (end - start) * p) for p in (0.6, 0.7, 0.8, 0.85)]
    masks = {}
    lower = start
    for name, upper in zip(("fit", "selection", "calibration", "policy"), cutoffs):
        masks[name] = (
            (~held)
            & (frame.timestamp >= lower)
            & (frame.timestamp < upper)
            & (frame.label_available_at < upper)
        )
        lower = upper
    masks["seen"] = (~held) & (frame.timestamp >= cutoffs[-1])
    masks["unseen"] = held & (frame.timestamp >= cutoffs[-1])
    for name, mask in masks.items():
        if frame.loc[mask, "label"].nunique() != 2:
            raise ValueError(f"{name} requires both classes")
    events = [
        Event(**{k: r[k] for k in Event.__dataclass_fields__}) for r in frame.to_dict("records")
    ]
    x = replay_features(events).reset_index(drop=True)
    # Independent oracle over complete histories of ten preselected accounts.
    selected = set(sorted(frame.account_id.unique())[:10])
    probe = [e for e in events if e.account_id in selected]
    pd.testing.assert_frame_equal(offline_features(probe), replay_features(probe), check_exact=True)
    enriched = pd.DataFrame([context(r) for r in frame.to_dict("records")])
    merchant = pd.DataFrame(delayed_features(frame, ~held))
    x = pd.concat([x, enriched, merchant], axis=1)
    # Keep feature snapshots locally for reproducible account-level analysis.
    x.to_pickle(output / "features.pkl")
    fit, selection, calibration, policy = [
        masks[n] for n in ("fit", "selection", "calibration", "policy")
    ]
    groups = {
        "legacy": FEATURES,
        "clock": FEATURES + CONTEXT[:2],
        "category": FEATURES + CONTEXT[:2] + CONTEXT[4:],
        "home": BASE + CONTEXT,
        "delayed_merchant": ENRICHED,
    }
    ablations, models = {}, {}
    for name, columns in groups.items():
        model = HistGradientBoostingClassifier(
            max_iter=120, max_leaf_nodes=15, l2_regularization=2, random_state=42
        )
        model.fit(x.loc[fit, columns], frame.loc[fit, "label"])
        threshold = float(np.quantile(model.predict_proba(x.loc[policy, columns])[:, 1], 0.95))
        ablations[name] = {
            "features": columns,
            "selection_ap": float(
                average_precision_score(
                    frame.loc[selection, "label"],
                    model.predict_proba(x.loc[selection, columns])[:, 1],
                )
            ),
            **{
                cohort: metrics(
                    frame.loc[masks[cohort], "label"],
                    model.predict_proba(x.loc[masks[cohort], columns])[:, 1],
                    threshold,
                )
                for cohort in ("seen", "unseen")
            },
        }
        models[name] = model
        print("Fitted ablation", name, flush=True)
    logistic = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1500, random_state=42))
    logistic.fit(x.loc[fit, ENRICHED], frame.loc[fit, "label"])
    selection_ap = {
        name: float(
            average_precision_score(
                frame.loc[selection, "label"], m.predict_proba(x.loc[selection, ENRICHED])[:, 1]
            )
        )
        for name, m in [("histogram_gbm", models["delayed_merchant"]), ("logistic", logistic)]
    }
    winner = max(selection_ap, key=selection_ap.get)
    raw_model = logistic if winner == "logistic" else models["delayed_merchant"]
    calibrated = CalibratedClassifierCV(FrozenEstimator(raw_model), method="sigmoid")
    calibrated.fit(x.loc[calibration, ENRICHED], frame.loc[calibration, "label"])
    policy_scores = calibrated.predict_proba(x.loc[policy, ENRICHED])[:, 1]
    threshold = float(np.quantile(policy_scores, 0.95))
    fpr_threshold = float(
        np.quantile(policy_scores[frame.loc[policy, "label"].to_numpy() == 0], 0.95)
    )
    evaluation = {}
    for cohort in ("seen", "unseen"):
        mask = masks[cohort]
        y = frame.loc[mask, "label"].to_numpy()
        scores = calibrated.predict_proba(x.loc[mask, ENRICHED])[:, 1]
        evaluation[cohort] = metrics(y, scores, threshold)
        evaluation[cohort]["bootstrap"] = cluster_intervals(
            y, scores, frame.loc[mask, "account_id"], threshold
        )
        evaluation[cohort]["policy_target_fpr_5pct"] = metrics(y, scores, fpr_threshold)
        evaluation[cohort]["uncalibrated_brier"] = float(
            brier_score_loss(y, raw_model.predict_proba(x.loc[mask, ENRICHED])[:, 1])
        )
    report = {
        "seed": 42,
        "dataset": f"Sparkov {frame.account_id.nunique()} active accounts; external synthetic profiles",
        "dataset_sha256": hashlib.sha256(
            Path(data).read_text(encoding="utf-8").encode()
        ).hexdigest(),
        "rows": len(frame),
        "accounts": int(frame.account_id.nunique()),
        "selected_model": winner,
        "selection_ap": selection_ap,
        "score_kind": "sigmoid_calibrated_synthetic",
        "threshold": threshold,
        "training_asof": cutoffs[-1],
        "policy_fpr_threshold": fpr_threshold,
        "cutoffs": cutoffs,
        "split_rows": {k: int(v.sum()) for k, v in masks.items()},
        "ablation": ablations,
        "evaluation": evaluation,
        "parity": "Independent full-history oracle for ten preselected accounts; all remaining rows use replay",
        "limits": [
            "Synthetic generator; not bank validation",
            "Merchant history excludes all held-out accounts, and uses only labels available strictly before each event",
            "AP lift adjusts against the base rate but does not fully remove prevalence dependence",
            "Bootstrap resamples accounts conditional on this fitted model; does not include retraining uncertainty",
            "All ablations reported; no test-based tuning. Served cold-start review policy is separate from threshold-only metrics",
        ],
    }
    reference = {}
    for name in ENRICHED:
        values = x.loc[fit, name].to_numpy()
        edges = np.unique(np.quantile(values, np.linspace(0.1, 0.9, 9)))
        reference[name] = {
            "inner_edges": edges.tolist(),
            "counts": np.histogram(values, [-np.inf, *edges, np.inf])[0].tolist(),
        }
    save_bundle(output / "model", calibrated, report, reference)
    (output / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(evaluation, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with threadpool_limits(limits=2):
        train(args.data, args.output)

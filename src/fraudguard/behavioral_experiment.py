"""Reproducible synthetic research track; never activates a production bundle."""

import hashlib
import json
import platform
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from fraudguard.behavioral import Event, offline_features, reasons, replay_features


def generate(seed=42, accounts=100, days=60):
    """Opaque account IDs and noisy simulated compromise episodes, not bank data.

    Labels originate from latent compromise state, not a threshold on features.
    Legitimate travel, purchases and bursts overlap fraudulent behavior.
    """
    rng = np.random.default_rng(seed)
    records = []
    for account in range(accounts):
        home = (float(rng.uniform(49, 56)), float(rng.uniform(-5, 1)))
        typical = float(rng.lognormal(3.5, 0.5))
        for day in range(days):
            compromised = rng.random() < 0.045
            burst = compromised or rng.random() < 0.07
            count = int(rng.integers(3, 7)) if burst else int(rng.poisson(1.4))
            start = day * 86400 + int(rng.integers(0, 80000))
            for _ in range(count):
                timestamp = start + int(rng.integers(0, 500 if burst else 6000))
                fraud = int(compromised and rng.random() < 0.85)
                unusual = fraud or rng.random() < 0.08
                amount = float(round(typical * rng.lognormal(1.0 if unusual else 0, 0.8), 2))
                latitude = float(np.clip(home[0] + rng.normal(0, 3 if unusual else 0.02), -90, 90))
                longitude = float(
                    np.clip(home[1] + rng.normal(0, 4 if unusual else 0.02), -180, 180)
                )
                merchant = int(rng.integers(0, 100 if unusual else 8))
                identifier = f"event-{len(records):08d}"
                event = Event(
                    identifier,
                    f"account-{account:04d}",
                    f"merchant-{merchant:03d}",
                    timestamp,
                    amount,
                    latitude,
                    longitude,
                )
                records.append(
                    {
                        **asdict(event),
                        "label": fraud,
                        "label_available_at": timestamp + int(rng.integers(1, 4)) * 86400,
                    }
                )
    return pd.DataFrame(records).sort_values(["timestamp", "event_id"]).reset_index(drop=True)


def split_masks(frame):
    """Entity holdout plus time cutoffs; fit/selection labels must have arrived."""
    start, end = int(frame.timestamp.min()), int(frame.timestamp.max()) + 1
    fit_end = start + int((end - start) * 0.6)
    selection_end = start + int((end - start) * 0.8)
    held = frame.account_id.map(
        lambda a: int(hashlib.sha256(a.encode()).hexdigest()[:8], 16) % 5 == 0
    )
    return {
        "fit": (~held) & (frame.timestamp < fit_end) & (frame.label_available_at < fit_end),
        "selection": (~held)
        & (frame.timestamp >= fit_end)
        & (frame.timestamp < selection_end)
        & (frame.label_available_at < selection_end),
        "test_seen": (~held) & (frame.timestamp >= selection_end),
        "test_unseen": held & (frame.timestamp >= selection_end),
    }, {"fit_end": fit_end, "selection_end": selection_end}


def measure(y, scores, threshold):
    if len(y) == 0:
        return {"rows": 0, "fraud_cases": 0, "average_precision": None}
    predicted = scores >= threshold
    return {
        "rows": len(y),
        "fraud_cases": int(np.sum(y)),
        "average_precision": float(average_precision_score(y, scores)) if np.sum(y) else None,
        "precision": float(precision_score(y, predicted, zero_division=0)),
        "recall": float(recall_score(y, predicted, zero_division=0)),
        "review_rate": float(np.mean(predicted)),
    }


def run(output, seed=42, accounts=100, days=60):
    if accounts < 20 or days < 20:
        raise ValueError("Use at least 20 accounts and 20 days for separated evaluation windows")
    frame = generate(seed, accounts, days)
    events = [
        Event(**r) for r in frame.drop(columns=["label", "label_available_at"]).to_dict("records")
    ]
    batch, online = offline_features(events), replay_features(events)
    pd.testing.assert_frame_equal(batch, online, check_exact=True)
    features = batch.reset_index(drop=True)
    masks, cutoffs = split_masks(frame)
    fit, selection = masks["fit"], masks["selection"]
    for name in ("fit", "selection"):
        if frame.loc[masks[name], "label"].nunique() != 2:
            raise ValueError(f"{name} needs both labels; increase accounts/days")
    candidates = {
        "logistic": make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1000, class_weight="balanced", random_state=seed),
        ),
        "histogram_gbm": HistGradientBoostingClassifier(
            max_iter=100, max_leaf_nodes=15, l2_regularization=2, random_state=seed
        ),
    }
    with threadpool_limits(limits=2):
        selection_ap = {}
        for name, model in candidates.items():
            model.fit(features.loc[fit], frame.loc[fit, "label"])
            selection_ap[name] = float(
                average_precision_score(
                    frame.loc[selection, "label"],
                    model.predict_proba(features.loc[selection])[:, 1],
                )
            )
        winner = max(selection_ap, key=selection_ap.get)
        model = candidates[winner]
        # Review capacity is specified before evaluating test outcomes.
        threshold = float(np.quantile(model.predict_proba(features.loc[selection])[:, 1], 0.95))
        results = {
            name: measure(
                frame.loc[mask, "label"], model.predict_proba(features.loc[mask])[:, 1], threshold
            )
            for name, mask in masks.items()
            if name.startswith("test_")
        }
        unseen_events = [e for e, keep in zip(events, masks["test_unseen"], strict=True) if keep]
        reset_features = replay_features(unseen_events).reset_index(drop=True)
        first_events = reset_features.cold_start == 1
        unseen_labels = frame.loc[masks["test_unseen"], "label"].reset_index(drop=True)
        results["unseen_first_event_no_history"] = measure(
            unseen_labels.loc[first_events],
            model.predict_proba(reset_features.loc[first_events])[:, 1],
            threshold,
        )
        baseline = make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1000, class_weight="balanced", random_state=seed),
        )
        baseline.fit(features.loc[fit, ["log_amount"]], frame.loc[fit, "label"])
        base_threshold = float(
            np.quantile(baseline.predict_proba(features.loc[selection, ["log_amount"]])[:, 1], 0.95)
        )
        results["amount_only_test_unseen"] = measure(
            frame.loc[masks["test_unseen"], "label"],
            baseline.predict_proba(features.loc[masks["test_unseen"], ["log_amount"]])[:, 1],
            base_threshold,
        )
        # Diagnostic only: random row splits violate deployment chronology.
        random_fit, rest = train_test_split(
            frame.index, test_size=0.4, stratify=frame.label, random_state=seed
        )
        random_select, random_test = train_test_split(
            rest, test_size=0.5, stratify=frame.loc[rest, "label"], random_state=seed
        )
        from sklearn.base import clone

        random_model = clone(model).fit(features.loc[random_fit], frame.loc[random_fit, "label"])
        random_threshold = float(
            np.quantile(random_model.predict_proba(features.loc[random_select])[:, 1], 0.95)
        )
        results["random_split_diagnostic"] = measure(
            frame.loc[random_test, "label"],
            random_model.predict_proba(features.loc[random_test])[:, 1],
            random_threshold,
        )
    dataset_csv = frame.to_csv(index=False, lineterminator="\n")
    report = {
        "dataset": "FraudGuard synthetic compromise simulation v1",
        "seed": seed,
        "runtime": {
            "python": platform.python_version(),
            **{p: version(p) for p in ("numpy", "pandas", "scikit-learn")},
        },
        "accounts": accounts,
        "days": days,
        "rows": len(frame),
        "dataset_sha256": hashlib.sha256(dataset_csv.encode()).hexdigest(),
        "exact_online_offline_parity": True,
        "feature_names": list(features.columns),
        "split_rows": {k: int(v.sum()) for k, v in masks.items()},
        "cutoffs": cutoffs,
        "selection_average_precision": selection_ap,
        "selected_model": winner,
        "selection_review_budget": 0.05,
        "threshold": threshold,
        "evaluation": results,
        "limits": [
            "Synthetic generator encodes behavioral fraud signals; scores do not establish real-bank performance.",
            "Unseen accounts are excluded from model fitting and selection, but have prior unlabeled transaction history at scoring time.",
            "Scores are not calibrated probabilities. Random split is a diagnostic, not an unbiased deployment estimate.",
            "Labels are delayed 1-3 days; test metrics assume eventual labels have arrived.",
            "This research reference is in-memory, single-owner and does not replace the deployed V1-V28 model.",
        ],
        "example_context": reasons(features.iloc[-1].to_dict()),
    }
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (destination / "synthetic-transactions.csv").write_text(dataset_csv, encoding="utf-8")
    return report

"""Reproducible training: train -> tune -> calibrate -> policy -> final test."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.frozen import FrozenEstimator
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, precision_recall_curve
from threadpoolctl import threadpool_limits

from fraudguard.artifacts import save_bundle
from fraudguard.data import FEATURES, prepare, temporal_split
from fraudguard.evaluation import bootstrap_ap, metrics, select_policy
from fraudguard.model import candidates
from fraudguard.monitoring import drift_report, reference_profile


def train(data_path, output, seed=42, max_review_rate=0.01, review_cost=2.0, missed_cost=100.0):
    output = Path(output)
    if output.exists():
        raise FileExistsError("Use a new run directory; existing releases are immutable")
    started = time.perf_counter()
    frame, quality = prepare(data_path)
    splits = temporal_split(frame)
    split_summary = {
        name: {
            "rows": len(df),
            "frauds": int(df.Class.sum()),
            "time_min": float(df.Time.min()),
            "time_max": float(df.Time.max()),
        }
        for name, df in splits.items()
    }
    train_df, tune = splits["train"], splits["tune"]
    experiments = []
    champion, best_score, best_name = None, -1.0, ""
    with threadpool_limits(limits=2):
        for name, estimator in candidates(seed):
            trial_start = time.perf_counter()
            estimator.fit(train_df[FEATURES], train_df.Class)
            p = estimator.predict_proba(tune[FEATURES])[:, 1]
            score = float(average_precision_score(tune.Class, p))
            experiments.append(
                {
                    "name": name,
                    "tune_average_precision": score,
                    "seconds": time.perf_counter() - trial_start,
                    "parameters": {
                        k: v
                        for k, v in estimator.get_params().items()
                        if isinstance(v, (int, float, str, bool, type(None)))
                    },
                }
            )
            print(f"{name}: tuning AP={score:.4f}", flush=True)
            if score > best_score:
                champion, best_score, best_name = estimator, score, name

        # Do not refit after selection: later windows stay independent of model fitting.
        calibration = splits["calibrate"]
        calibrated = CalibratedClassifierCV(FrozenEstimator(champion), method="sigmoid")
        calibrated.fit(calibration[FEATURES], calibration.Class)
        policy_df = splits["policy"]
        policy_p = calibrated.predict_proba(policy_df[FEATURES])[:, 1]
        policy = select_policy(policy_df.Class, policy_p, max_review_rate, review_cost, missed_cost)
        test = splits["test"]
        test_p = calibrated.predict_proba(test[FEATURES])[:, 1]
        raw_p = champion.predict_proba(test[FEATURES])[:, 1]
        evaluation = metrics(test.Class, test_p, policy["threshold"], review_cost, missed_cost)
        evaluation["ap_interval"] = bootstrap_ap(test.Class, test_p, seed)
        evaluation["raw_brier"] = float(np.mean((raw_p - test.Class.to_numpy()) ** 2))
        importance = permutation_importance(
            calibrated,
            policy_df[FEATURES],
            policy_df.Class,
            scoring="average_precision",
            n_repeats=3,
            random_state=seed,
            n_jobs=1,
        )
        feature_importance = sorted(
            [
                {"feature": f, "ap_drop_mean": float(m), "ap_drop_std": float(s)}
                for f, m, s in zip(
                    FEATURES, importance.importances_mean, importance.importances_std
                )
            ],
            key=lambda x: -x["ap_drop_mean"],
        )
        reference = reference_profile(train_df)
        report = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "dataset": quality,
            "splits": split_summary,
            "purged_rows": len(frame) - sum(len(df) for df in splits.values()),
            "selection_metric": "tune average precision",
            "champion": best_name,
            "experiments": experiments,
            "policy": policy,
            "test": evaluation,
            "permutation_importance_on_policy": feature_importance,
            "test_vs_train_drift": (
                drift_report(reference, test)
                if len(test) >= 1000
                else {"alert": None, "status": "insufficient_data", "rows": len(test)}
            ),
            "slice_metrics": {},
        }
        # Training-derived amount boundaries; slices are descriptive, never selection criteria.
        amount_cut = float(train_df.Amount.quantile(0.9))
        for label, mask in {
            "amount_at_or_below_train_p90": test.Amount <= amount_cut,
            "amount_above_train_p90": test.Amount > amount_cut,
        }.items():
            if test.loc[mask, "Class"].nunique() == 2:
                report["slice_metrics"][label] = metrics(
                    test.loc[mask, "Class"],
                    test_p[mask],
                    policy["threshold"],
                    review_cost,
                    missed_cost,
                )
        report["amount_slice_boundary"] = amount_cut
        report["duration_seconds"] = time.perf_counter() - started
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + quality["sha256"][:8]
        save_bundle(
            output,
            calibrated,
            policy,
            reference,
            {
                "run_id": run_id,
                "champion": best_name,
                "dataset_sha256": quality["sha256"],
                "created_utc": report["created_utc"],
                "seed": seed,
            },
        )
        (output / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        from fraudguard.reporting import render_report

        render_report(report, output, test.Class.to_numpy(), test_p)
        # Public benchmark feature vector only; no label is exposed in the request example.
        example = {
            "transactions": [
                dict(
                    transaction_id="example-001",
                    **{k: float(v) for k, v in test.iloc[0][FEATURES].to_dict().items()},
                )
            ]
        }
        (output / "example_request.json").write_text(json.dumps(example, indent=2))
        precision, recall, _ = precision_recall_curve(test.Class, test_p)
        actual, predicted = calibration_curve(test.Class, test_p, n_bins=10, strategy="uniform")
        (output / "curves.json").write_text(
            json.dumps(
                {
                    "precision": precision[:: max(1, len(precision) // 1000)].tolist(),
                    "recall": recall[:: max(1, len(recall) // 1000)].tolist(),
                    "calibration_observed": actual.tolist(),
                    "calibration_predicted": predicted.tolist(),
                }
            )
        )
    return report

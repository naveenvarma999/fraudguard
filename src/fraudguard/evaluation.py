"""Metrics and a validation-only, cost-sensitive review policy."""

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


def metrics(y, p, threshold, review_cost=2.0, missed_fraud_cost=100.0):
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    pred = p >= threshold
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {
        "rows": len(y),
        "frauds": int(y.sum()),
        "prevalence": float(y.mean()),
        "average_precision": float(average_precision_score(y, p)),
        "roc_auc": float(roc_auc_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "review_rate": float(pred.mean()),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "simulated_cost": float(review_cost * pred.sum() + missed_fraud_cost * fn),
        "no_review_cost": float(missed_fraud_cost * y.sum()),
    }


def select_policy(y, p, max_review_rate=0.01, review_cost=2.0, missed_fraud_cost=100.0):
    if not 0 < max_review_rate <= 1 or review_cost < 0 or missed_fraud_cost <= 0:
        raise ValueError("Invalid policy costs or capacity")
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=int)
    order = np.argsort(-p, kind="stable")
    scores, labels = p[order], y[order]
    # Evaluate only ends of score ties so >= threshold matches policy exactly.
    ends = np.flatnonzero(np.r_[scores[:-1] != scores[1:], True])
    reviewed = ends + 1
    caught = np.cumsum(labels)[ends]
    costs = reviewed * review_cost + (y.sum() - caught) * missed_fraud_cost
    valid = reviewed / len(y) <= max_review_rate
    options = [(float(y.sum() * missed_fraud_cost), 0, 1.0000000001)]
    options.extend(
        (float(c), int(n), float(t))
        for c, n, t in zip(costs[valid], reviewed[valid], scores[ends][valid])
    )
    cost, count, threshold = min(options)
    return {
        "threshold": threshold,
        "validation_cost": cost,
        "validation_review_rate": count / len(y),
        "max_review_rate": max_review_rate,
        "review_cost": review_cost,
        "missed_fraud_cost": missed_fraud_cost,
        "assumption": "Every reviewed fraud is caught; flat costs in arbitrary units",
    }


def bootstrap_ap(y, p, seed=42, iterations=200):
    """Stratified row bootstrap; not a time-dependent confidence guarantee."""
    y, p = np.asarray(y), np.asarray(p)
    rng = np.random.default_rng(seed)
    positives, negatives = np.flatnonzero(y == 1), np.flatnonzero(y == 0)
    values = []
    for _ in range(iterations):
        idx = np.r_[rng.choice(positives, len(positives)), rng.choice(negatives, len(negatives))]
        values.append(average_precision_score(y[idx], p[idx]))
    return {
        "low": float(np.quantile(values, 0.025)),
        "high": float(np.quantile(values, 0.975)),
        "iterations": iterations,
        "method": "stratified row bootstrap; conditional on prevalence",
    }

"""Offline population stability checks; drift is not proof of quality loss."""

import numpy as np

from fraudguard.data import FEATURES, validate


def reference_profile(frame):
    result = {}
    for feature in FEATURES:
        values = frame[feature].to_numpy()
        inner = np.unique(np.quantile(values, np.linspace(0.1, 0.9, 9)))
        bins = np.r_[-np.inf, inner, np.inf]
        counts, _ = np.histogram(values, bins)
        result[feature] = {"inner_edges": inner.tolist(), "counts": counts.tolist()}
    return result


def drift_report(reference, current):
    validate(current)
    if len(current) < 1000:
        raise ValueError("Drift monitoring requires at least 1,000 rows")
    features = {}
    for feature in FEATURES:
        ref = reference[feature]
        counts, _ = np.histogram(current[feature], np.r_[-np.inf, ref["inner_edges"], np.inf])
        expected = np.asarray(ref["counts"], float) + 0.5
        observed = counts.astype(float) + 0.5
        expected /= expected.sum()
        observed /= observed.sum()
        psi = float(np.sum((observed - expected) * np.log(observed / expected)))
        features[feature] = {"psi": psi, "investigate": psi >= 0.2}
    return {
        "rows": len(current),
        "heuristic_threshold": 0.2,
        "alert": any(v["investigate"] for v in features.values()),
        "features": features,
        "interpretation": "Heuristic distribution shift only; join delayed labels for quality",
    }

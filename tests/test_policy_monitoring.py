import numpy as np
import pytest

from fraudguard.evaluation import metrics, select_policy
from fraudguard.monitoring import drift_report, reference_profile


def test_threshold_exactly_minimizes_feasible_cost_with_ties():
    y = np.array([1, 0, 1, 0, 1, 0])
    p = np.array([0.9, 0.9, 0.8, 0.5, 0.1, 0.1])
    policy = select_policy(y, p, max_review_rate=0.5)
    choices = [1.0000000001, *np.unique(p)]
    feasible = [t for t in choices if np.mean(p >= t) <= 0.5]
    costs = [2 * np.sum(p >= t) + 100 * np.sum((y == 1) & (p < t)) for t in feasible]
    assert policy["validation_cost"] == min(costs)
    assert np.mean(p >= policy["threshold"]) <= 0.5


def test_no_review_is_valid_when_tie_exceeds_capacity():
    policy = select_policy([0, 1, 0, 1], [0.5] * 4, max_review_rate=0.1)
    assert policy["threshold"] > 1
    assert policy["validation_review_rate"] == 0


def test_review_cost_charged_for_every_review():
    result = metrics([0, 1, 0, 1], [0.1, 0.9, 0.8, 0.2], 0.5)
    assert result["confusion"] == {"tn": 1, "fp": 1, "fn": 1, "tp": 1}
    assert result["simulated_cost"] == 104


def test_drift_detects_shift_and_handles_constant_feature(frame):
    frame["V2"] = 0
    ref = reference_profile(frame)
    assert not drift_report(ref, frame)["alert"]
    shifted = frame.copy()
    shifted["V1"] += 20
    assert drift_report(ref, shifted)["features"]["V1"]["investigate"]
    with pytest.raises(ValueError, match="1,000"):
        drift_report(ref, frame.iloc[:20])


@pytest.mark.parametrize("capacity", [0, -1, 1.1])
def test_invalid_capacity(capacity):
    with pytest.raises(ValueError):
        select_policy([0, 1], [0.1, 0.9], max_review_rate=capacity)

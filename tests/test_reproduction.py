import copy
import json
from pathlib import Path

import numpy as np
import pytest

from fraudguard.behavioral_experiment import measure
from fraudguard.reproduce import compare


def test_undefined_cold_start_metrics_are_null():
    result = measure(np.array([0, 0]), np.array([0.1, 0.9]), 0.5)
    assert result["status"] == "insufficient_data"
    assert result["precision"] is result["recall"] is result["average_precision"] is None
    assert result["review_rate"] == 0.5


@pytest.mark.parametrize("field", ["dataset_sha256", "threshold", "evaluation"])
def test_reproduction_rejects_changed_hash_or_metrics(field):
    report = json.loads(
        (Path(__file__).parents[1] / "artifacts/behavioral/report.json").read_text()
    )
    changed = copy.deepcopy(report)
    changed[field] = "changed"
    with pytest.raises(ValueError, match="Reproduction"):
        compare(report, changed)

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from fraudguard.artifacts import save_bundle
from fraudguard.data import FEATURES
from fraudguard.model import TransactionFeatures
from fraudguard.monitoring import reference_profile


@pytest.fixture
def frame():
    rng = np.random.default_rng(123)
    result = pd.DataFrame(rng.normal(size=(4000, 28)), columns=FEATURES[:-1])
    result["Amount"] = rng.lognormal(3, 1, len(result))
    result["Time"] = np.arange(len(result)) * 10
    result["Class"] = (np.arange(len(result)) % 10 == 0).astype(int)
    result["V1"] += result.Class * 3
    return result


@pytest.fixture
def bundle(tmp_path, frame):
    model = make_pipeline(TransactionFeatures(), LogisticRegression(max_iter=300))
    model.fit(frame[FEATURES], frame.Class)
    path = tmp_path / "release"
    save_bundle(
        path,
        model,
        {"threshold": 0.5, "review_cost": 2.0, "missed_fraud_cost": 100.0},
        reference_profile(frame),
        {"run_id": "test-v1", "champion": "test-logistic"},
    )
    return path, model


@pytest.fixture
def payload(frame):
    return {"transactions": [{"transaction_id": "test-001", **frame.iloc[0][FEATURES].to_dict()}]}

import json

import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from fraudguard.artifacts import load_bundle
from fraudguard.data import FEATURES
from fraudguard.model import TransactionFeatures
from fraudguard.training import train


def test_end_to_end_training_artifact_and_report(tmp_path, frame, monkeypatch):
    monkeypatch.setattr(
        "fraudguard.training.candidates",
        lambda seed: iter(
            [
                (
                    "smoke-logistic",
                    make_pipeline(TransactionFeatures(), LogisticRegression(max_iter=200)),
                )
            ]
        ),
    )
    data, output = tmp_path / "transactions.csv", tmp_path / "run"
    frame.to_csv(data, index=False)
    report = train(data, output)
    model, manifest = load_bundle(output)
    assert model.predict_proba(frame.iloc[:3][FEATURES]).shape == (3, 2)
    assert manifest["dataset_sha256"] == report["dataset"]["sha256"]
    assert (output / "report.html").exists()
    assert (output / "evaluation.png").stat().st_size > 1000
    assert json.loads((output / "evaluation.json").read_text())["test"]["rows"] > 0
    with pytest.raises(FileExistsError):
        train(data, output)

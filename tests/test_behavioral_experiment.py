import pandas as pd

from fraudguard.behavioral_experiment import generate, run, split_masks


def test_entity_holdout_and_delayed_label_cutoffs():
    frame = generate(seed=42, accounts=30, days=30)
    masks, cutoffs = split_masks(frame)
    development = set(frame.loc[masks["fit"] | masks["selection"], "account_id"])
    assert development.isdisjoint(frame.loc[masks["test_unseen"], "account_id"])
    assert frame.loc[masks["fit"], "label_available_at"].max() < cutoffs["fit_end"]
    assert frame.loc[masks["selection"], "label_available_at"].max() < cutoffs["selection_end"]
    assert frame.loc[masks["test_seen"], "timestamp"].min() >= cutoffs["selection_end"]
    assert sum(m.astype(int) for m in masks.values()).max() == 1
    pd.testing.assert_frame_equal(frame, generate(seed=42, accounts=30, days=30))


def test_complete_experiment_records_evidence_without_production_artifact(tmp_path):
    report = run(tmp_path, seed=42, accounts=30, days=30)
    assert report["exact_online_offline_parity"] is True
    assert report["selected_model"] in report["selection_average_precision"]
    assert report["evaluation"]["test_unseen"]["rows"] > 0
    assert (tmp_path / "report.json").is_file()
    assert (tmp_path / "synthetic-transactions.csv").is_file()
    assert not (tmp_path / "model.joblib").exists()

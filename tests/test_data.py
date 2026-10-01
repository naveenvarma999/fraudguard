import numpy as np
import pytest

from fraudguard.data import FEATURES, prepare, temporal_split, validate
from fraudguard.model import TransactionFeatures


def test_time_windows_disjoint_and_purged(frame):
    parts = temporal_split(frame)
    for a, b in zip(list(parts.values())[:-1], list(parts.values())[1:]):
        assert a.Time.max() + 60 <= b.Time.min()
        assert set(a.index).isdisjoint(b.index)
    assert sum(map(len, parts.values())) < len(frame)


def test_equal_timestamps_never_cross_boundary(frame):
    frame["Time"] = (frame.Time // 100) * 100
    parts = temporal_split(frame)
    names = list(parts)
    for i, name in enumerate(names):
        for later in names[i + 1 :]:
            assert set(parts[name].Time).isdisjoint(parts[later].Time)


@pytest.mark.parametrize("change", ["missing", "nan", "infinite", "negative", "label", "string"])
def test_invalid_data_rejected(frame, change):
    if change == "missing":
        frame = frame.drop(columns=["V1"])
    elif change == "nan":
        frame.loc[0, "V1"] = np.nan
    elif change == "infinite":
        frame.loc[0, "V1"] = np.inf
    elif change == "negative":
        frame.loc[0, "Amount"] = -1
    elif change == "label":
        frame.loc[0, "Class"] = 2
    else:
        frame["V1"] = frame.V1.astype(str)
    with pytest.raises(ValueError):
        validate(frame, labeled=True)


def test_deduplication_and_conflicting_labels(tmp_path, frame):
    import pandas as pd

    path = tmp_path / "data.csv"
    pd.concat([frame, frame.iloc[:1]]).to_csv(path, index=False)
    clean, report = prepare(path)
    assert len(clean) == len(frame)
    assert report["duplicates_removed"] == 1
    conflict = frame.iloc[:1].copy()
    conflict["Class"] = 1 - conflict.Class
    pd.concat([frame, conflict]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="conflicting"):
        prepare(path)


def test_transform_order_and_input_immutability(frame):
    transformer = TransactionFeatures().fit(frame)
    before = frame.copy(deep=True)
    result = transformer.transform(frame)
    np.testing.assert_allclose(result[:, -1], np.log1p(frame.Amount))
    np.testing.assert_allclose(result, transformer.transform(frame[FEATURES[::-1]]))
    assert frame.equals(before)


def test_sparse_positive_window_fails(frame):
    frame.loc[frame.Time > 30000, "Class"] = 0
    with pytest.raises(ValueError, match="needs"):
        temporal_split(frame)

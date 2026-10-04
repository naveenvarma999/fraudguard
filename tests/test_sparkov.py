import pandas as pd

from fraudguard.sparkov import normalize


def test_sparkov_drops_personal_fields_and_normalizes_utc():
    raw = pd.DataFrame(
        {
            "trans_num": ["b", "a"],
            "cc_num": ["fake-card", "fake-card"],
            "merchant": ["store", "store"],
            "trans_date": ["2024-01-01"] * 2,
            "trans_time": ["00:00:02", "00:00:01"],
            "amt": [10, 20],
            "merch_lat": [51, 51],
            "merch_long": [0, 0],
            "is_fraud": [0, 1],
            "first": ["PERSONAL", "PERSONAL"],
            "ssn": ["PRIVATE", "PRIVATE"],
        }
    )
    frame = normalize(raw)
    assert list(frame.event_id) == ["a", "b"]
    assert frame.timestamp.iloc[0] == 1704067201
    assert (frame.label_available_at - frame.timestamp == 172800).all()
    assert not {"first", "ssn", "cc_num"} & set(frame.columns)
    assert frame.account_id.nunique() == 1
    assert "fake-card" not in frame.to_csv(index=False)

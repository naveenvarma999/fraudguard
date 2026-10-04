from dataclasses import replace

import pandas as pd
import pytest

from fraudguard.behavioral import Event, OnlineFeatures, offline_features, replay_features


def event(identifier, timestamp, account="account-a", amount=10.0, merchant="shop"):
    return Event(identifier, account, merchant, timestamp, amount, 51.5, -0.1)


def test_window_boundaries_ties_and_cold_start():
    events = [
        event("a", 0),
        event("b", 600),
        event("c", 600),
        event("d", 3601),
        event("e", 30 * 86400 + 1),
    ]
    batch = offline_features(events)
    pd.testing.assert_frame_equal(batch, replay_features(events), check_exact=True)
    assert batch.loc["a", "cold_start"] == 1
    assert batch.loc["b", "transactions_10m"] == 1  # Left boundary included.
    assert batch.loc["c", "transactions_10m"] == 1  # Equal-time b excluded.
    assert batch.loc["d", "transactions_1h"] == 2
    assert batch.loc["e", "history_count_30d"] == 3  # a expired.


def test_future_and_other_accounts_cannot_change_past_features():
    prior = [event("a", 10), event("b", 20)]
    expected = offline_features(prior)
    actual = offline_features(
        [*prior, event("future", 21, amount=999), event("other", 15, account="b")]
    )
    pd.testing.assert_frame_equal(expected, actual.loc[expected.index], check_exact=True)


def test_retry_is_idempotent_but_conflicts_and_late_events_are_rejected():
    engine = OnlineFeatures()
    first = event("a", 10)
    original = engine.process(first)
    engine.process(event("b", 20))
    assert engine.process(first) == original
    with pytest.raises(ValueError, match="Conflicting"):
        engine.process(replace(first, amount=20))
    with pytest.raises(ValueError, match="Late"):
        engine.process(event("late", 9))
    assert engine.process(event("c", 30))["history_count_30d"] == 2


def test_replay_recovery_and_input_order():
    events = [
        event(str(i), i * 13, account=f"account-{i % 4}", amount=float(i)) for i in range(100)
    ]
    pd.testing.assert_frame_equal(
        offline_features(list(reversed(events))), replay_features(events), check_exact=True
    )
    one = OnlineFeatures()
    for e in events[:50]:
        one.process(e)
    recovered = OnlineFeatures()
    for e in events[:50]:
        recovered.process(e)
    assert one.process(events[50]) == recovered.process(events[50])


@pytest.mark.parametrize(
    "field,value", [("amount", float("nan")), ("amount", -1), ("latitude", 91), ("timestamp", 1.5)]
)
def test_invalid_raw_inputs(field, value):
    with pytest.raises(ValueError):
        replace(event("a", 1), **{field: value})

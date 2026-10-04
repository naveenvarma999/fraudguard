"""Point-in-time behavioral features: independent batch oracle and streaming replay.

Research implementation, not a distributed feature store. Identifiers are opaque
synthetic account tokens, never payment card numbers. All windows exclude time t.
"""

import math
from collections import defaultdict, deque
from dataclasses import asdict, dataclass

import pandas as pd

WINDOW = 30 * 86400
FEATURES = [
    "log_amount",
    "transactions_10m",
    "transactions_1h",
    "mean_amount_30d",
    "amount_to_mean_30d",
    "new_merchant_30d",
    "distance_from_previous_km",
    "history_count_30d",
    "cold_start",
]


@dataclass(frozen=True)
class Event:
    event_id: str
    account_id: str
    merchant_id: str
    timestamp: int
    amount: float
    latitude: float
    longitude: float

    def __post_init__(self):
        for name in ("event_id", "account_id", "merchant_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not 1 <= len(value) <= 100:
                raise ValueError(f"Invalid {name}")
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ValueError("timestamp must be a nonnegative UTC Unix second")
        for name, low, high in (
            ("amount", 0, 1e9),
            ("latitude", -90, 90),
            ("longitude", -180, 180),
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError(f"Invalid {name}")


def distance(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    h = (
        math.sin((b - a) / 2) ** 2
        + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


class OnlineFeatures:
    """Event-time ordered, single-owner state; retries return the original result.

    Keep one processor per ordered partition. Late events are rejected rather than
    silently revising predictions. Replay the source log to recover after restart.
    The deduplication map is intentionally unbounded in this research reference.
    """

    def __init__(self):
        self.history = defaultdict(deque)
        self.seen = {}
        self.watermark = (-1, "")

    def process(self, event):
        if event.event_id in self.seen:
            original, features = self.seen[event.event_id]
            if original != event:
                raise ValueError("Conflicting duplicate event ID")
            return dict(features)
        order = (event.timestamp, event.event_id)
        if order < self.watermark:
            raise ValueError("Late event: replay in (timestamp,event_id) order")
        history = self.history[event.account_id]
        while history and history[0].timestamp < event.timestamp - WINDOW:
            history.popleft()
        prior = [r for r in history if r.timestamp < event.timestamp]
        mean = math.fsum(r.amount for r in prior) / len(prior) if prior else 0.0
        previous = prior[-1] if prior else None
        features = {
            "log_amount": math.log1p(event.amount),
            "transactions_10m": sum(r.timestamp >= event.timestamp - 600 for r in prior),
            "transactions_1h": sum(r.timestamp >= event.timestamp - 3600 for r in prior),
            "mean_amount_30d": mean,
            "amount_to_mean_30d": event.amount / max(mean, 1.0) if prior else 1.0,
            "new_merchant_30d": int(not any(r.merchant_id == event.merchant_id for r in prior)),
            "distance_from_previous_km": distance(
                previous.latitude, previous.longitude, event.latitude, event.longitude
            )
            if previous
            else 0.0,
            "history_count_30d": len(prior),
            "cold_start": int(not prior),
        }
        history.append(event)
        self.watermark = order
        self.seen[event.event_id] = (event, dict(features))
        return features


def offline_features(events):
    """Independent dataframe oracle: timestamp masks, not the streaming processor.

    Returns rows in canonical event order. The O(n^2) within-account oracle is
    deliberately simple enough to audit; it is not intended for large datasets.
    """
    ordered = sorted(events, key=lambda e: (e.timestamp, e.event_id))
    if len({e.event_id for e in ordered}) != len(ordered):
        raise ValueError("Offline input requires unique event IDs")
    if not ordered:
        return pd.DataFrame(columns=FEATURES)
    frame = pd.DataFrame([asdict(e) for e in ordered])
    groups = {name: rows for name, rows in frame.groupby("account_id", sort=False)}
    results = []
    for event in ordered:
        group = groups[event.account_id]
        prior = group[
            (group.timestamp < event.timestamp) & (group.timestamp >= event.timestamp - WINDOW)
        ]
        count = len(prior)
        mean = math.fsum(prior.amount) / count if count else 0.0
        last = prior.iloc[-1] if count else None
        results.append(
            {
                "log_amount": math.log1p(event.amount),
                "transactions_10m": int((prior.timestamp >= event.timestamp - 600).sum()),
                "transactions_1h": int((prior.timestamp >= event.timestamp - 3600).sum()),
                "mean_amount_30d": mean,
                "amount_to_mean_30d": event.amount / max(mean, 1.0) if count else 1.0,
                "new_merchant_30d": int(event.merchant_id not in set(prior.merchant_id)),
                "distance_from_previous_km": distance(
                    last.latitude, last.longitude, event.latitude, event.longitude
                )
                if count
                else 0.0,
                "history_count_30d": count,
                "cold_start": int(not count),
            }
        )
    return pd.DataFrame(results, columns=FEATURES, index=[e.event_id for e in ordered])


def replay_features(events):
    processor = OnlineFeatures()
    ordered = sorted(events, key=lambda e: (e.timestamp, e.event_id))
    return pd.DataFrame(
        [processor.process(e) for e in ordered],
        columns=FEATURES,
        index=[e.event_id for e in ordered],
    )


def reasons(features):
    """Observable behavioral context, not SHAP or a causal fraud explanation."""
    result = []
    if features["cold_start"]:
        result.append("No prior account transactions in the 30-day window")
    else:
        result.append(f"{int(features['transactions_10m'])} prior transactions in the last 10 minutes")
        result.append(
            f"Amount is {features['amount_to_mean_30d']:.2f} times the prior 30-day mean (minimum denominator 1)"
        )
    if features["new_merchant_30d"]:
        result.append("Merchant not seen for this account in the prior 30 days")
    return result

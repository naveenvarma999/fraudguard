"""Context and delayed-label features with explicit point-in-time cutoffs."""

import math

from fraudguard.behavioral import FEATURES, distance

CATEGORIES = [
    "entertainment",
    "food_dining",
    "gas_transport",
    "grocery_net",
    "grocery_pos",
    "health_fitness",
    "home",
    "kids_pets",
    "misc_net",
    "misc_pos",
    "personal_care",
    "shopping_net",
    "shopping_pos",
    "travel",
]
CONTEXT = [
    "hour_sin",
    "hour_cos",
    "home_distance_km",
    "home_missing",
    "category_unknown",
    *["category_" + c for c in CATEGORIES],
]
MERCHANT = ["merchant_fraud_rate", "merchant_label_count"]
BASE = [f for f in FEATURES if f != "distance_from_previous_km"]
ENRICHED = BASE + CONTEXT + MERCHANT


def context(raw):
    angle = 2 * math.pi * (raw["timestamp"] % 86400) / 86400
    home = raw.get("home_latitude") is not None and raw.get("home_longitude") is not None
    category = raw.get("category", "unknown")
    return {
        "hour_sin": math.sin(angle),
        "hour_cos": math.cos(angle),
        "home_distance_km": distance(
            raw["home_latitude"], raw["home_longitude"], raw["latitude"], raw["longitude"]
        )
        if home
        else 0.0,
        "home_missing": int(not home),
        "category_unknown": int(category not in CATEGORIES),
        **{"category_" + c: int(category == c) for c in CATEGORIES},
    }


def merchant_stats(count, frauds):
    # Fixed prior and strength; never estimated from future or held-out labels.
    return {"merchant_fraud_rate": (frauds + 0.2) / (count + 20), "merchant_label_count": count}


def known_merchant(db, merchant, timestamp):
    rows = db.execute(
        """SELECT h.fraud FROM label_history h WHERE h.merchant=? AND h.available_at<? AND h.event_timestamp<?
      AND NOT EXISTS(SELECT 1 FROM label_history n WHERE n.prediction_id=h.prediction_id AND n.available_at<? AND (n.available_at>h.available_at OR (n.available_at=h.available_at AND n.id>h.id)))""",
        (merchant, timestamp, timestamp, timestamp),
    ).fetchall()
    return merchant_stats(len(rows), sum(r[0] for r in rows))


def delayed_features(frame, eligible):
    """Independent batch label sweep; strict availability before the event."""
    from collections import defaultdict

    arrivals = sorted(
        (int(r.label_available_at), int(r.timestamp), str(r.merchant_id), int(r.label))
        for r in frame.loc[eligible].itertuples()
    )
    totals = defaultdict(lambda: [0, 0])
    cursor, result = 0, []
    for row in frame.itertuples():
        while cursor < len(arrivals) and arrivals[cursor][0] < row.timestamp:
            available, timestamp, merchant, fraud = arrivals[cursor]
            if timestamp >= available:
                raise ValueError("Labels must arrive after the source event")
            totals[merchant][0] += 1
            totals[merchant][1] += fraud
            cursor += 1
        result.append(merchant_stats(*totals[row.merchant_id]))
    return result

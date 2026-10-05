"""Normalize Sparkov generator or Kaggle-format exports without retaining PII."""

import hashlib

import pandas as pd


def normalize(raw, enriched=False):
    clock = (
        raw["trans_date_trans_time"]
        if "trans_date_trans_time" in raw
        else raw.trans_date + " " + raw.trans_time
    )
    times = pd.to_datetime(clock, utc=True, errors="raise").astype("int64") // 1_000_000_000

    def token(value):
        return hashlib.sha256(str(value).encode()).hexdigest()

    result = pd.DataFrame(
        {
            "event_id": raw.trans_num.astype(str),
            "account_id": raw.cc_num.map(token),
            "merchant_id": raw.merchant.map(token),
            "timestamp": times,
            "amount": pd.to_numeric(raw.amt),
            "latitude": pd.to_numeric(raw.merch_lat),
            "longitude": pd.to_numeric(raw.merch_long),
            "label": pd.to_numeric(raw.is_fraud),
            "label_available_at": times + 2 * 86400,
        }
    )
    if enriched:
        result["category"] = raw.category.astype(str)
        result["home_latitude"] = pd.to_numeric(raw.lat)
        result["home_longitude"] = pd.to_numeric(raw.long)
    if (
        not result.label.isin([0, 1]).all()
        or result.event_id.duplicated().any()
        or result.isna().any().any()
    ):
        raise ValueError("Sparkov rows require unique IDs, binary labels and complete data")
    return result.sort_values(["timestamp", "event_id"]).reset_index(drop=True)

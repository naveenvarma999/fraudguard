"""Strict data contracts and disjoint chronological partitions."""

import hashlib
import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

FEATURES = [f"V{i}" for i in range(1, 29)] + ["Amount"]
SCHEMA_VERSION = "ulb-pca-v1"
DATA_URL = "https://storage.googleapis.com/download.tensorflow.org/data/creditcard.csv"
EXPECTED_DATA_SHA256 = "76274b691b16a6c49d3f159c883398e03ccd6d1ee12d9d8ee38f4b4b98551a89"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(path):
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".download")
    try:
        with urllib.request.urlopen(DATA_URL, timeout=120) as response:
            with temporary.open("wb") as out:
                while block := response.read(1024 * 1024):
                    out.write(block)
        if sha256(temporary) != EXPECTED_DATA_SHA256:
            raise ValueError(
                "Downloaded benchmark checksum changed; inspect source before accepting"
            )
        validate(pd.read_csv(temporary), labeled=True)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    provenance = {"url": DATA_URL, "sha256": sha256(path)}
    path.with_suffix(".provenance.json").write_text(json.dumps(provenance, indent=2))
    return provenance


def validate(frame, labeled=False):
    expected = FEATURES + (["Time", "Class"] if labeled else [])
    missing = set(expected) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("Data must contain at least one row")
    for column in expected:
        if not pd.api.types.is_numeric_dtype(frame[column]):
            raise ValueError(f"{column} must be numeric")
    if not np.isfinite(frame[expected].to_numpy(dtype=float)).all():
        raise ValueError("Data contains missing or non-finite values")
    if (frame.Amount < 0).any():
        raise ValueError("Amount must be non-negative")
    if labeled:
        if (frame.Time < 0).any() or not frame.Class.isin([0, 1]).all():
            raise ValueError("Time must be non-negative and Class must be 0 or 1")
    return frame


def prepare(path):
    raw = validate(pd.read_csv(path), labeled=True)
    # Same transaction features with conflicting outcomes indicate corrupt labels.
    key = FEATURES + ["Time"]
    if raw.groupby(key, sort=False).Class.nunique().gt(1).any():
        raise ValueError("Identical transaction rows have conflicting labels")
    clean = raw.drop_duplicates().sort_values("Time", kind="stable").reset_index(drop=True)
    return clean, {
        "raw_rows": len(raw),
        "unique_rows": len(clean),
        "duplicates_removed": len(raw) - len(clean),
        "fraud_count": int(clean.Class.sum()),
        "fraud_rate": float(clean.Class.mean()),
        "sha256": sha256(path),
    }


def temporal_split(frame, gap_seconds=60):
    """Keep equal timestamps together; purge first 60 seconds of each later window."""
    if gap_seconds < 0:
        raise ValueError("gap_seconds cannot be negative")
    frame = frame.sort_values("Time", kind="stable")
    times = frame.Time.to_numpy()
    cuts = [float(times[int(len(frame) * q)]) for q in [0.50, 0.65, 0.75, 0.85]]
    bounds = [-np.inf, *cuts, np.inf]
    names = ["train", "tune", "calibrate", "policy", "test"]
    result = {}
    for i, name in enumerate(names):
        low = bounds[i] + (gap_seconds if i else 0)
        part = frame[(frame.Time >= low) & (frame.Time < bounds[i + 1])].copy()
        if len(part) < 100 or part.Class.nunique() != 2 or int(part.Class.sum()) < 5:
            raise ValueError(f"{name} needs >=100 rows and >=5 frauds, with both classes")
        result[name] = part
    return result

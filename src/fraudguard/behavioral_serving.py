"""Behavioral model bundles and scoring against server-owned prior history."""

import hashlib
import json
import math
from pathlib import Path

import joblib
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from fraudguard.behavioral import FEATURES, OnlineFeatures, reasons


class RawEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    event_id: str = Field(min_length=1, max_length=100)
    account_id: str = Field(min_length=1, max_length=100)
    merchant_id: str = Field(min_length=1, max_length=100)
    timestamp: int = Field(ge=0, le=253402300799)
    amount: float = Field(ge=0, le=1e9)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class BehavioralRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    transaction: RawEvent


def save_bundle(directory, model, report, reference=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, directory / "model.joblib")
    digest = hashlib.sha256((directory / "model.joblib").read_bytes()).hexdigest()
    manifest = {
        "schema": "behavioral-v1",
        "features": FEATURES,
        "model_sha256": digest,
        "version": "behavioral-" + digest[:16],
        "threshold": report["threshold"],
        "dataset": report["dataset"],
        "dataset_sha256": report["dataset_sha256"],
        "seed": report["seed"],
        "score_kind": "uncalibrated",
        "history_source": "server-owned",
        "model_type": report["selected_model"],
        "reference": reference or {},
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def load_bundle(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256((directory / "model.joblib").read_bytes()).hexdigest()
    if (
        manifest["schema"] != "behavioral-v1"
        or manifest["features"] != FEATURES
        or manifest["model_sha256"] != digest
        or not math.isfinite(manifest["threshold"])
        or not 0 <= manifest["threshold"] <= 1
    ):
        raise ValueError("Invalid behavioral bundle")
    # This is operator-controlled serialized code, never a user-uploaded model.
    model = joblib.load(directory / "model.joblib")
    if list(model.feature_names_in_) != FEATURES or list(model.classes_) != [0, 1]:
        raise ValueError("Behavioral model feature/class contract mismatch")
    return model, manifest


def score_event(bundle, current, history):
    from collections import deque

    engine = OnlineFeatures()
    engine.history[current.account_id] = deque(history)
    features = engine.process(current)
    model, manifest = bundle
    risk = float(model.predict_proba(pd.DataFrame([features], columns=FEATURES))[0, 1])
    if not math.isfinite(risk) or not 0 <= risk <= 1:
        raise ValueError("Invalid behavioral score")
    return {
        "event_id": current.event_id,
        "model_version": manifest["version"],
        "risk_score": risk,
        "score_kind": "uncalibrated",
        "threshold": manifest["threshold"],
        "decision": "review" if risk >= manifest["threshold"] else "pass",
        "features": features,
        "context": reasons(features),
        "history_source": "server-owned",
        "cold_start": bool(features["cold_start"]),
    }

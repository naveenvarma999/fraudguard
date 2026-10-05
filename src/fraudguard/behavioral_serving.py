"""Behavioral model bundles and scoring against server-owned prior history."""

import hashlib
import json
import math
from pathlib import Path

import joblib
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from fraudguard.behavioral import FEATURES, OnlineFeatures, reasons
from fraudguard.enriched import ENRICHED, context, merchant_stats


class RawEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    event_id: str = Field(min_length=1, max_length=100)
    account_id: str = Field(min_length=1, max_length=100)
    merchant_id: str = Field(min_length=1, max_length=100)
    timestamp: int = Field(ge=0, le=253402300799)
    amount: float = Field(ge=0, le=1e9)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    category: str = Field(default="unknown", max_length=50)
    home_latitude: float | None = Field(default=None, ge=-90, le=90)
    home_longitude: float | None = Field(default=None, ge=-180, le=180)


class BehavioralRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    transaction: RawEvent


def save_bundle(directory, model, report, reference=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, directory / "model.joblib")
    digest = hashlib.sha256((directory / "model.joblib").read_bytes()).hexdigest()
    manifest = {
        "schema": "behavioral-v2" if list(model.feature_names_in_) == ENRICHED else "behavioral-v1",
        "features": list(model.feature_names_in_),
        "model_sha256": digest,
        "version": "behavioral-" + digest[:16],
        "threshold": report["threshold"],
        "dataset": report["dataset"],
        "dataset_sha256": report["dataset_sha256"],
        "seed": report["seed"],
        "score_kind": report.get("score_kind", "uncalibrated"),
        "history_source": "server-owned",
        "model_type": report["selected_model"],
        "reference": reference or {},
        "training_asof": report.get("training_asof"),
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def load_bundle(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256((directory / "model.joblib").read_bytes()).hexdigest()
    if (
        manifest["schema"] not in ("behavioral-v1", "behavioral-v2")
        or manifest["features"] != (ENRICHED if manifest["schema"] == "behavioral-v2" else FEATURES)
        or manifest["model_sha256"] != digest
        or not math.isfinite(manifest["threshold"])
        or not 0 <= manifest["threshold"] <= 1
    ):
        raise ValueError("Invalid behavioral bundle")
    # This is operator-controlled serialized code, never a user-uploaded model.
    model = joblib.load(directory / "model.joblib")
    if list(model.feature_names_in_) != manifest["features"] or list(model.classes_) != [0, 1]:
        raise ValueError("Behavioral model feature/class contract mismatch")
    return model, manifest


def score_event(bundle, current, history, raw=None, merchant=None):
    from collections import deque

    engine = OnlineFeatures()
    engine.history[current.account_id] = deque(history)
    features = engine.process(current)
    model, manifest = bundle
    if manifest["schema"] == "behavioral-v2":
        if raw is None:
            raise ValueError("Enriched scoring requires event context")
        features.update(context(raw))
        features.update(merchant or merchant_stats(0, 0))
        features = {name: features[name] for name in ENRICHED}
    risk = float(model.predict_proba(pd.DataFrame([features], columns=manifest["features"]))[0, 1])
    if not math.isfinite(risk) or not 0 <= risk <= 1:
        raise ValueError("Invalid behavioral score")
    return {
        "event_id": current.event_id,
        "model_version": manifest["version"],
        "risk_score": risk,
        "score_kind": manifest["score_kind"],
        "threshold": manifest["threshold"],
        "decision": "review" if risk >= manifest["threshold"] else "pass",
        "features": features,
        "context": reasons(features),
        "history_source": "server-owned",
        "cold_start": bool(features["cold_start"]),
    }

"""Versioned local model bundles with integrity and runtime checks."""

import json
import platform
from pathlib import Path

import joblib
import sklearn

from fraudguard.data import FEATURES, SCHEMA_VERSION, sha256


def save_bundle(path, model, policy, reference, metadata):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    joblib.dump(model, path / "model.joblib", compress=3)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "features": FEATURES,
        "model_sha256": sha256(path / "model.joblib"),
        "sklearn_version": sklearn.__version__,
        "python_version": platform.python_version(),
        "policy": policy,
        "reference": reference,
        **metadata,
    }
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_bundle(path):
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["features"] != FEATURES:
        raise ValueError("Artifact schema mismatch")
    if manifest["sklearn_version"] != sklearn.__version__:
        raise ValueError("Artifact sklearn version does not match serving runtime")
    if sha256(path / "model.joblib") != manifest["model_sha256"]:
        raise ValueError("Artifact checksum mismatch")
    # Pickle-based formats execute code. Only load trusted, access-controlled releases.
    return joblib.load(path / "model.joblib"), manifest

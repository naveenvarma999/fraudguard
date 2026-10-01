"""Private scoring worker. No accounts, sessions, audit writes or public routes."""

import os
import secrets
import threading
from collections import OrderedDict
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Header, HTTPException
from pydantic import Field
from threadpoolctl import threadpool_limits

from fraudguard.api import BodyLimitMiddleware, PredictionRequest
from fraudguard.artifacts import load_bundle
from fraudguard.data import FEATURES
from fraudguard.releases import bundle_digest


class ScoreRequest(PredictionRequest):
    version: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


def create_worker(registry=None, key=None):
    root = Path(registry or os.environ["REGISTRY_DIR"]).resolve()
    secret = key or os.getenv("INFERENCE_KEY", "")
    if len(secret) < 32:
        raise ValueError("Configure a private INFERENCE_KEY of at least 32 characters")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimitMiddleware)
    lock = threading.Lock()
    slots = threading.BoundedSemaphore(2)
    cache = OrderedDict()

    @app.get("/health/ready")
    def ready():
        if not root.is_dir():
            raise HTTPException(503, "Registry unavailable")
        return {"status": "ready"}

    @app.post("/internal/score")
    def score(payload: ScoreRequest, x_inference_key: str = Header(default="")):
        if not secrets.compare_digest(secret.encode(), x_inference_key.encode()):
            raise HTTPException(401, "Invalid worker credentials")
        if not slots.acquire(blocking=False):
            raise HTTPException(503, "Worker busy")
        try:
            cache_key = (payload.version, payload.digest)
            with lock:
                if cache_key not in cache:
                    path = (root / payload.version).resolve()
                    if path.parent != root or bundle_digest(path) != payload.digest:
                        raise ValueError("Model integrity failure")
                    model, manifest = load_bundle(path)
                    if manifest["run_id"] != payload.version:
                        raise ValueError("Model version mismatch")
                    cache[cache_key] = model
                    if len(cache) > 2:
                        cache.popitem(last=False)
                cache.move_to_end(cache_key)
                model = cache[cache_key]
            with threadpool_limits(limits=1):
                probabilities = model.predict_proba(
                    pd.DataFrame([t.model_dump() for t in payload.transactions])[FEATURES]
                )[:, 1]
            return {"version": payload.version, "probabilities": probabilities.tolist()}
        except Exception:
            raise HTTPException(503, "Worker could not score the requested release") from None
        finally:
            slots.release()

    return app

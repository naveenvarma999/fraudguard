"""Authenticated inference API with bounded inputs and process-local telemetry."""

import json
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator
from threadpoolctl import threadpool_limits

from fraudguard import mfa
from fraudguard.artifacts import load_bundle
from fraudguard.auth import session_user
from fraudguard.behavioral_serving import BehavioralRequest
from fraudguard.behavioral_serving import load_bundle as load_behavioral
from fraudguard.behavioral_serving import score as behavioral_score
from fraudguard.dashboard import mount_dashboard, prepare_demo
from fraudguard.data import FEATURES, SCHEMA_VERSION
from fraudguard.limits import Admission, quota
from fraudguard.operations import Telemetry
from fraudguard.pool import Pool
from fraudguard.releases import Runtime
from fraudguard.store import Store
from fraudguard.workspace import mount_workspace

logger = logging.getLogger("fraudguard")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


Transaction = create_model(
    "Transaction",
    __base__=StrictModel,
    transaction_id=(str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")),
    **{f: (float, Field(ge=-10000, le=10000)) for f in FEATURES[:-1]},
    Amount=(float, Field(ge=0, le=1e9)),
)


class PredictionRequest(StrictModel):
    transactions: list[Transaction] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [t.transaction_id for t in self.transactions]
        if len(ids) != len(set(ids)):
            raise ValueError("transaction_id must be unique within a batch")
        return self


class Prediction(BaseModel):
    prediction_id: str | None = None
    transaction_id: str
    fraud_probability: float
    decision: str


class PredictionResponse(BaseModel):
    model_version: str
    schema_version: str
    threshold: float
    predictions: list[Prediction]


class BodyLimitMiddleware:
    """Limit actual bytes, including chunked bodies, before JSON parsing."""

    def __init__(self, app, limit=262144):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.limit:
                return await JSONResponse({"detail": "Request body too large"}, status_code=413)(
                    scope, receive, send
                )
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


def create_app(model_dir=None, api_key=None, state_dir=None):
    release = Path(model_dir or os.getenv("MODEL_DIR", "artifacts/benchmark"))
    key = api_key if api_key is not None else os.getenv("API_KEY", "")
    registry = CollectorRegistry()
    predictions = Counter(
        "fraudguard_predictions_total", "Scored transactions", ["decision"], registry=registry
    )
    behavioral_predictions = Counter(
        "fraudguard_behavioral_predictions_total",
        "Scored behavioral events",
        ["decision"],
        registry=registry,
    )
    requests = Counter(
        "fraudguard_requests_total", "Prediction requests", ["status"], registry=registry
    )
    latency = Histogram(
        "fraudguard_inference_seconds",
        "Model inference latency",
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0),
        registry=registry,
    )
    risk = Histogram(
        "fraudguard_risk",
        "Distribution of predicted probabilities",
        buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0),
        registry=registry,
    )
    slots = threading.BoundedSemaphore(4)
    admission = Admission()
    stateless_quota = Admission(capacity=60, refill=1)

    @asynccontextmanager
    async def lifespan(app):
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        logger.propagate = False
        if len(key) < 16:
            raise RuntimeError("Set API_KEY to a secret with at least 16 characters")
        monitor_secret = os.getenv("MONITOR_KEY", "")
        if monitor_secret and (
            len(monitor_secret) < 16
            or secrets.compare_digest(monitor_secret.encode(), key.encode())
        ):
            raise RuntimeError(
                "MONITOR_KEY must be distinct from API_KEY and at least 16 characters"
            )
        if mfa.required():
            mfa.cipher()
        app.state.pool = None
        urls = os.getenv("INFERENCE_WORKERS", "")
        if urls:
            worker_key = os.getenv("INFERENCE_KEY", "")
            if worker_key in (key, monitor_secret):
                raise RuntimeError("INFERENCE_KEY must be distinct from public and monitoring keys")
            app.state.pool = Pool(urls, worker_key)
        app.state.behavioral = None
        behavioral_path = Path(os.getenv("BEHAVIORAL_MODEL_DIR", "artifacts/behavioral_service"))
        if behavioral_path.exists() or os.getenv("BEHAVIORAL_MODEL_DIR"):
            app.state.behavioral = load_behavioral(behavioral_path)
        app.state.model = None
        app.state.demo = None
        app.state.store = (
            Store(state_dir or os.environ["STATE_DIR"])
            if state_dir or os.getenv("STATE_DIR")
            else None
        )
        if app.state.pool and not app.state.store:
            raise RuntimeError("Inference workers require a persistent approved model registry")
        app.state.runtime = Runtime(app, app.state.store, release)
        app.state.telemetry = Telemetry()
        try:
            app.state.model, app.state.manifest = load_bundle(release)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            logger.error("Model unavailable or invalid; readiness is disabled")
        try:
            app.state.runtime.restore()
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            app.state.model = None
            logger.error("Active registry release invalid; readiness disabled")
        with threadpool_limits(limits=2):
            if app.state.model is not None:
                app.state.demo = prepare_demo(app.state.model, app.state.manifest)
            yield

    app = FastAPI(
        title="FraudGuard",
        version="2.4.0",
        lifespan=lifespan,
        description="Benchmark fraud risk scoring. Review decisions are recommendations.",
    )
    app.add_middleware(BodyLimitMiddleware)
    app.state.inference_slots = slots
    mount_dashboard(app, release)

    def authorize(
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        if authorization and authorization.startswith("Bearer ") and app.state.store:
            return session_user(app.state.store, authorization[7:])
        if x_api_key and secrets.compare_digest(x_api_key.encode(), key.encode()):
            return {"name": "service", "role": "service"}
        raise HTTPException(status_code=401, detail="Invalid credentials")

    def monitor_authorize(
        x_monitor_key: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ):
        monitor_key = os.getenv("MONITOR_KEY", "")
        if (
            len(monitor_key) >= 16
            and x_monitor_key
            and secrets.compare_digest(monitor_key.encode(), x_monitor_key.encode())
        ):
            return {"name": "watchdog", "role": "monitor"}
        return authorize(x_api_key, authorization)

    mount_workspace(app, authorize, monitor_authorize)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Never echo input values or include raw transaction features in error logs.
        return JSONResponse(
            status_code=422,
            content={"detail": [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]},
        )

    @app.middleware("http")
    async def count_requests(request: Request, call_next):
        started = time.perf_counter()
        try:
            if request.url.path not in ("/health/live", "/health/ready") and not admission.take():
                response = JSONResponse(
                    {"detail": "Server request limit reached"},
                    status_code=429,
                    headers={"Retry-After": "1"},
                )
            else:
                response = await call_next(request)
        except Exception:
            if request.url.path in ("/v1/predict", "/v1/behavioral/predict"):
                app.state.telemetry.record(500, time.perf_counter() - started)
            raise
        if request.url.path in ("/v1/predict", "/v1/behavioral/predict"):
            app.state.telemetry.record(response.status_code, time.perf_counter() - started)
        if request.url.path in ("/v1/predict", "/v1/behavioral/predict"):
            requests.labels(str(response.status_code)).inc()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path in ("/", "/workspace") or request.url.path.startswith("/assets/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
            )
        if request.url.path.startswith(("/v1/", "/ui/", "/ops/", "/auth/")) or request.url.path in (
            "/",
            "/workspace",
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        if app.state.model is None:
            raise HTTPException(status_code=503, detail="Model unavailable")
        if app.state.store:
            try:
                app.state.store.query("SELECT value FROM settings WHERE key='active_release'")
            except Exception:
                raise HTTPException(503, "Workspace storage unavailable") from None
        return {"status": "ready"}

    @app.get("/v1/model", dependencies=[Depends(authorize)])
    def model_info():
        ready()
        manifest = app.state.manifest
        return {k: manifest[k] for k in ("run_id", "schema_version", "champion", "policy")}

    @app.get("/metrics", dependencies=[Depends(authorize)])
    def prometheus_metrics():
        return Response(generate_latest(registry), headers={"Content-Type": CONTENT_TYPE_LATEST})

    @app.get("/v1/behavioral/model", dependencies=[Depends(authorize)])
    def behavioral_info():
        if app.state.behavioral is None:
            raise HTTPException(503, "Behavioral model is not configured")
        return app.state.behavioral[1]

    @app.post("/v1/behavioral/predict")
    def behavioral_predict(payload: BehavioralRequest, actor=Depends(authorize)):
        if app.state.behavioral is None:
            raise HTTPException(503, "Behavioral model is not configured")
        if not slots.acquire(blocking=False):
            raise HTTPException(503, "Inference capacity busy", headers={"Retry-After": "1"})
        started = time.perf_counter()
        try:
            if app.state.store:
                quota(app.state.store, actor["name"], 1)
            elif not stateless_quota.take():
                raise HTTPException(
                    429, "Prediction rate limit reached", headers={"Retry-After": "1"}
                )
            try:
                result = behavioral_score(app.state.behavioral, payload)
                behavioral_predictions.labels(result["decision"]).inc()
                return result
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        finally:
            latency.observe(time.perf_counter() - started)
            slots.release()

    @app.post("/v1/predict", response_model=PredictionResponse, response_model_exclude_none=True)
    def predict(payload: PredictionRequest, actor=Depends(authorize)):
        ready()
        if not slots.acquire(blocking=False):
            raise HTTPException(
                status_code=503,
                detail="Inference capacity busy; retry with backoff",
                headers={"Retry-After": "1"},
            )
        started = time.perf_counter()
        try:
            if app.state.store:
                quota(app.state.store, actor["name"], len(payload.transactions))
            elif not stateless_quota.take():
                raise HTTPException(
                    429, "Prediction rate limit reached", headers={"Retry-After": "1"}
                )
            with app.state.runtime.lock:
                model, manifest = app.state.model, app.state.manifest
                version = manifest["run_id"]
                digest = (
                    app.state.store.query("SELECT digest FROM releases WHERE id=?", (version,))[0][
                        "digest"
                    ]
                    if app.state.pool
                    else None
                )
            frame = pd.DataFrame(
                [t.model_dump(exclude={"transaction_id"}) for t in payload.transactions]
            )
            p = (
                app.state.pool.score(
                    [t.model_dump() for t in payload.transactions], version, digest
                )
                if app.state.pool
                else model.predict_proba(frame[FEATURES])[:, 1]
            )
            threshold = manifest["policy"]["threshold"]
            ids = (
                app.state.store.record(
                    actor["name"],
                    [t.model_dump() for t in payload.transactions],
                    p,
                    manifest,
                )
                if app.state.store
                else [None] * len(p)
            )
            results = []
            for transaction, probability, prediction_id in zip(payload.transactions, p, ids):
                decision = "review" if probability >= threshold else "pass"
                predictions.labels(decision).inc()
                risk.observe(float(probability))
                results.append(
                    Prediction(
                        prediction_id=prediction_id,
                        transaction_id=transaction.transaction_id,
                        fraud_probability=float(probability),
                        decision=decision,
                    )
                )
            return PredictionResponse(
                model_version=manifest["run_id"],
                schema_version=SCHEMA_VERSION,
                threshold=threshold,
                predictions=results,
            )
        except HTTPException:
            raise
        except Exception:
            logger.error("Inference failed", exc_info=False)
            raise HTTPException(status_code=500, detail="Inference failed") from None
        finally:
            latency.observe(time.perf_counter() - started)
            slots.release()
            logger.info(
                json.dumps(
                    {
                        "event": "inference_complete",
                        "batch_size": len(payload.transactions),
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    }
                )
            )

    return app

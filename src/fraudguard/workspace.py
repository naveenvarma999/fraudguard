"""Named-user workspace routes with ownership checks and append-only action history."""

import json
import sqlite3
import time

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from fraudguard import mfa
from fraudguard.auth import add_user, login, password_hash, session_user
from fraudguard.dashboard import ASSETS
from fraudguard.operations import drift, quality


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Credentials(Input):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=1, max_length=128)


class LoginCredentials(Credentials):
    otp: str = Field(default="", max_length=40)


class Enrollment(Input):
    token: str = Field(min_length=20, max_length=128)


class Confirmation(Enrollment):
    code: str = Field(pattern=r"^[0-9]{6}$")


class NewUser(Credentials):
    role: str = Field(pattern=r"^(admin|analyst)$")


class PasswordChange(Input):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class Review(Input):
    disposition: str = Field(pattern=r"^(open|confirmed_fraud|cleared)$")
    note: str = Field(min_length=1, max_length=1000)


class Label(Input):
    fraud: int = Field(ge=0, le=1)
    source: str = Field(min_length=3, max_length=300)


class UserStatus(Input):
    active: bool


def mount_workspace(app, authorize, monitor_authorize):
    def store():
        if app.state.store is None:
            raise HTTPException(
                503, "Workspace persistence is disabled. Configure STATE_DIR and restart."
            )
        return app.state.store

    def user(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Sign in to the workspace")
        return session_user(store(), authorization[7:])

    def admin(request: Request, actor=Depends(user)):
        if actor["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
        if request.method != "GET" and time.time() - actor["issued_at"] > 900:
            raise HTTPException(
                401, "Sign in again before an administrator change (15-minute security window)"
            )
        return actor

    def owned(db, identifier, actor):
        row = db.execute("SELECT * FROM predictions WHERE id=?", (identifier,)).fetchone()
        if not row or (actor["role"] != "admin" and row["owner"] != actor["name"]):
            raise HTTPException(404, "Prediction not found")
        return row

    @app.get("/workspace", include_in_schema=False)
    def page():
        return FileResponse(ASSETS / "workspace.html")

    @app.post("/auth/login")
    def sign_in(payload: LoginCredentials, request: Request):
        return login(
            store(),
            payload.username,
            payload.password,
            request.client.host if request.client else "unknown",
            payload.otp,
        )

    @app.post("/auth/mfa/setup")
    def mfa_setup(payload: Enrollment):
        return mfa.setup(store(), payload.token)

    @app.post("/auth/mfa/confirm")
    def mfa_confirm(payload: Confirmation):
        return mfa.confirm(store(), payload.token, payload.code)

    @app.get("/auth/me")
    def me(actor=Depends(user)):
        return {"name": actor["name"], "role": actor["role"]}

    @app.post("/auth/logout")
    def sign_out(actor=Depends(user)):
        with store().connect() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (actor["token_hash"],))
            store().audit(db, actor["name"], "logout")
        return {"status": "signed_out"}

    @app.post("/auth/password")
    def change_password(payload: PasswordChange, actor=Depends(user)):
        import secrets

        with store().connect() as db:
            row = db.execute("SELECT password FROM users WHERE name=?", (actor["name"],)).fetchone()
            if not secrets.compare_digest(
                password_hash(payload.current_password, row["password"].split(":")[0]),
                row["password"],
            ):
                raise HTTPException(400, "Current password is incorrect")
            db.execute(
                "UPDATE users SET password=? WHERE name=?",
                (password_hash(payload.new_password), actor["name"]),
            )
            db.execute("DELETE FROM sessions WHERE username=?", (actor["name"],))
            db.execute("DELETE FROM enrollments WHERE username=?", (actor["name"],))
            store().audit(db, actor["name"], "password.changed")
        return {"status": "password_changed_sign_in_again"}

    @app.get("/ops/users")
    def users(actor=Depends(admin)):
        return store().query("SELECT name,role,active,created FROM users ORDER BY name")

    @app.post("/ops/users", status_code=201)
    def create_user(payload: NewUser, actor=Depends(admin)):
        try:
            add_user(store(), payload.username, payload.password, payload.role, actor["name"])
        except (ValueError, sqlite3.IntegrityError):
            raise HTTPException(
                400, "Username exists or password does not meet the 12–128 character requirement"
            ) from None
        return {"status": "created"}

    @app.patch("/ops/users/{name}")
    def user_status(name: str, payload: UserStatus, actor=Depends(admin)):
        if name == actor["name"]:
            raise HTTPException(400, "You cannot deactivate your own account")
        with store().connect() as db:
            if (
                db.execute(
                    "UPDATE users SET active=? WHERE name=?", (int(payload.active), name)
                ).rowcount
                != 1
            ):
                raise HTTPException(404, "User not found")
            db.execute("DELETE FROM sessions WHERE username=?", (name,))
            db.execute("DELETE FROM enrollments WHERE username=?", (name,))
            store().audit(db, actor["name"], "user.status", name, {"active": payload.active})
        return {"status": "updated"}

    @app.get("/ops/overview")
    def overview(actor=Depends(user)):
        now = time.time()
        start = (int(now // 86400) - 6) * 86400
        clause = "1=1" if actor["role"] == "admin" else "owner=?"
        params = () if actor["role"] == "admin" else (actor["name"],)
        with store().connect() as db:
            db.execute("BEGIN")
            totals = dict(
                db.execute(
                    f"SELECT count(*) AS transactions,coalesce(sum(decision='review'),0) AS flagged FROM predictions WHERE {clause} AND at>=?",
                    (*params, start),
                ).fetchone()
            )
            backlog = db.execute(
                f"SELECT count(*) FROM predictions WHERE {clause} AND decision='review' AND review='open'",
                params,
            ).fetchone()[0]
            days = [
                dict(row)
                for row in db.execute(
                    f"SELECT cast(at/86400 AS INTEGER)*86400 AS day,count(*) AS transactions,sum(decision='review') AS flagged FROM predictions WHERE {clause} AND at>=? GROUP BY day ORDER BY day",
                    (*params, start),
                )
            ]
            recent = [
                dict(row)
                for row in db.execute(
                    f"SELECT id,transaction_id,at,probability,decision,review FROM predictions WHERE {clause} ORDER BY at DESC,id DESC LIMIT 5",
                    params,
                )
            ]
        with app.state.runtime.lock:
            available = app.state.model is not None
            version = app.state.manifest["run_id"] if available else None
        by_day = {r["day"]: r for r in days}
        return {
            **totals,
            "backlog": backlog,
            "scope": "All accounts" if actor["role"] == "admin" else "Your submissions",
            "since": start,
            "as_of": now,
            "recent": recent,
            "daily": [
                by_day.get(
                    start + i * 86400, {"day": start + i * 86400, "transactions": 0, "flagged": 0}
                )
                for i in range(7)
            ],
            "model": {
                "available": available,
                "version": version,
                "quality": "Performance requires verified outcomes; availability is not a quality score.",
            },
            "worker_count": len(app.state.pool.urls) if app.state.pool else 0,
        }

    @app.get("/ops/predictions/{identifier}")
    def prediction_detail(
        identifier: str, actor=Depends(user), before: int = Query(default=9223372036854775807, ge=1)
    ):
        with store().connect() as db:
            db.execute("BEGIN")
            record = dict(owned(db, identifier, actor))
            behavioral = db.execute(
                "SELECT response FROM behavioral_events WHERE prediction_id=?", (identifier,)
            ).fetchone()
            label = db.execute(
                "SELECT fraud,source,actor,at FROM labels WHERE prediction_id=?", (identifier,)
            ).fetchone()
            events = [
                dict(row)
                for row in db.execute(
                    "SELECT id,at,actor,action,detail FROM audit WHERE resource=? AND action IN ('prediction.reviewed','label.recorded','label.corrected') AND id<? ORDER BY id DESC LIMIT 51",
                    (identifier, before),
                )
            ]
        return {
            "prediction": record,
            "behavioral": json.loads(behavioral[0]) if behavioral else None,
            "label": dict(label) if label else None,
            "events": events[:50],
            "next_before": events[49]["id"] if len(events) > 50 else None,
        }

    @app.get("/ops/predictions")
    def history(
        actor=Depends(user),
        before: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        decision: str = Query(default="all", pattern=r"^(all|review|pass)$"),
    ):
        clauses, params = ["1=1"], []
        if before:
            with store().connect() as db:
                cursor = owned(db, before, actor)
                clauses.append("(p.at<? OR (p.at=? AND p.id<?))")
                params.extend([cursor["at"], cursor["at"], cursor["id"]])
        if actor["role"] != "admin":
            clauses.append("p.owner=?")
            params.append(actor["name"])
        if decision != "all":
            clauses.append("p.decision=?")
            params.append(decision)
        return store().query(
            "SELECT p.*,l.fraud,l.source AS label_source FROM predictions p LEFT JOIN labels l ON p.id=l.prediction_id WHERE "
            + " AND ".join(clauses)
            + " ORDER BY p.at DESC,p.id DESC LIMIT ?",
            (*params, limit),
        )

    @app.post("/ops/predictions/{identifier}/review")
    def review(identifier: str, payload: Review, actor=Depends(user)):
        with store().connect() as db:
            owned(db, identifier, actor)
            db.execute(
                "UPDATE predictions SET review=?,note=? WHERE id=?",
                (payload.disposition, payload.note, identifier),
            )
            store().audit(
                db, actor["name"], "prediction.reviewed", identifier, payload.model_dump()
            )
        return {"status": "reviewed"}

    @app.post("/ops/predictions/{identifier}/label")
    def label(identifier: str, payload: Label, actor=Depends(user)):
        with store().connect() as db:
            owned(db, identifier, actor)
            old = db.execute(
                "SELECT fraud,source FROM labels WHERE prediction_id=?", (identifier,)
            ).fetchone()
            db.execute(
                "INSERT INTO labels VALUES(?,?,?,?,?) ON CONFLICT(prediction_id) DO UPDATE SET fraud=excluded.fraud,source=excluded.source,actor=excluded.actor,at=excluded.at",
                (identifier, payload.fraud, payload.source, actor["name"], time.time()),
            )
            store().audit(
                db,
                actor["name"],
                "label.corrected" if old else "label.recorded",
                identifier,
                {**payload.model_dump(), "previous": dict(old) if old else None},
            )
        return {"status": "label_recorded"}

    @app.get("/ops/audit")
    def audit(
        actor=Depends(user),
        before: int = Query(default=9223372036854775807, ge=1),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        clause, params = (
            ("", ()) if actor["role"] == "admin" else (" AND actor=?", (actor["name"],))
        )
        return store().query(
            "SELECT * FROM audit WHERE id<?" + clause + " ORDER BY id DESC LIMIT ?",
            (before, *params, limit),
        )

    @app.get("/ops/monitoring")
    def monitoring(days: int = Query(default=7, ge=1, le=30), actor=Depends(monitor_authorize)):
        if actor["role"] not in ("admin", "service", "monitor"):
            raise HTTPException(403, "Administrator access required")
        with app.state.runtime.lock:
            if app.state.model is None:
                raise HTTPException(503, "Model unavailable")
            manifest = app.state.manifest
            return {
                "model_version": manifest["run_id"],
                "behavioral": {
                    "model_version": app.state.behavioral[1]["version"],
                    "quality": quality(store(), app.state.behavioral[1]["version"], days),
                    "drift": drift(
                        store(),
                        {**app.state.behavioral[1], "run_id": app.state.behavioral[1]["version"]},
                        days,
                    )
                    if app.state.behavioral[1].get("reference")
                    else {"status": "reference_not_available"},
                }
                if app.state.behavioral
                else None,
                "workers": app.state.pool.status() if app.state.pool else [],
                "drift": drift(store(), manifest, days),
                "quality": quality(store(), manifest["run_id"], days),
                "telemetry": app.state.telemetry.snapshot(store().directory),
                "recovery": store().query(
                    "SELECT key,value FROM settings WHERE key LIKE 'backup_%' OR key='delivery_enabled'"
                ),
                "delivery": store().query(
                    "SELECT status,count(*) AS count FROM deliveries GROUP BY status"
                ),
                "alerts": store().query("SELECT * FROM alerts ORDER BY active DESC,name"),
            }

    @app.get("/ops/releases")
    def releases(actor=Depends(admin)):
        records = store().query(
            "SELECT id,gates,submitted_by,submitted_at,status,approved_by,approved_at FROM releases ORDER BY submitted_at DESC"
        )
        for record in records:
            record["gates"] = json.loads(record["gates"])
        return {
            "active": app.state.manifest.get("run_id") if app.state.model is not None else None,
            "releases": records,
        }

    @app.post("/ops/releases/{version}/approve")
    def approve(version: str, actor=Depends(admin)):
        try:
            app.state.runtime.approve(version, actor["name"])
        except (ValueError, OSError, KeyError):
            raise HTTPException(
                409, "Approval refused: check gates, immutable files and independent approver"
            ) from None
        return {"status": "approved"}

    @app.post("/ops/releases/{version}/activate")
    def activate(version: str, actor=Depends(admin)):
        try:
            return app.state.runtime.activate(version, actor["name"])
        except (ValueError, OSError, KeyError):
            raise HTTPException(
                409,
                "Activation refused: release must be approved, intact and different from the active version",
            ) from None

    @app.post("/ops/rollback")
    def rollback(actor=Depends(admin)):
        try:
            return app.state.runtime.activate(None, actor["name"], rollback=True)
        except (ValueError, OSError, KeyError):
            raise HTTPException(409, "No valid previous release is available") from None

"""Read-only deployment verification: readiness, version, authorization and backups."""

import argparse
import json
import os
import time
import urllib.error
import urllib.request


def check(base, expected, key, require_backup=False):
    base = base.rstrip("/")

    def get(path, authenticated=False, expected_status=200):
        headers = {"X-API-Key": key} if authenticated else {}
        try:
            with urllib.request.urlopen(
                urllib.request.Request(base + path, headers=headers), timeout=10
            ) as r:
                status, body, response_headers = r.status, r.read(), r.headers
        except urllib.error.HTTPError as exc:
            status, body, response_headers = exc.code, exc.read(), exc.headers
        if status != expected_status:
            raise ValueError(f"{path}: expected HTTP {expected_status}, received {status}")
        return body, response_headers

    ready, _ = get("/health/ready")
    if json.loads(ready).get("status") != "ready":
        raise ValueError("Model/storage not ready")
    spec, _ = get("/openapi.json")
    if json.loads(spec)["info"]["version"] != expected:
        raise ValueError("Unexpected application version; an old image may still be running")
    _, headers = get("/workspace")
    if (
        "frame-ancestors 'none'" not in headers.get("Content-Security-Policy", "")
        or headers.get("Cache-Control") != "no-store"
    ):
        raise ValueError("Workspace security headers missing")
    get("/auth/me", expected_status=401)
    get("/v1/model", expected_status=401)
    model, _ = get("/v1/model", True)
    demo, _ = get("/ui/demo")
    if json.loads(model)["run_id"] != json.loads(demo)["model_version"]:
        raise ValueError("Model and public demo versions disagree")
    state, _ = get("/ops/monitoring", True)
    state = json.loads(state)
    if require_backup:
        settings = {r["key"]: r["value"] for r in state["recovery"]}
        latest = json.loads(settings.get("backup_latest", "null"))
        if not latest or not latest.get("verified") or time.time() - latest["created"] > 26 * 3600:
            raise ValueError("A fresh verified backup is required")
    return {
        "status": "passed",
        "application": expected,
        "model": json.loads(model)["run_id"],
        "checks": [
            "readiness",
            "version",
            "security headers",
            "unauthenticated access denied",
            "authenticated model",
            "demo parity",
            "monitoring",
        ]
        + (["verified backup freshness"] if require_backup else []),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--expected-version", default="2.6.0")
    parser.add_argument("--require-backup", action="store_true")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()
    if not 1 <= args.attempts <= 12 or not 1 <= args.interval <= 30:
        parser.error("Use 1–12 attempts and a 1–30 second interval")
    for attempt in range(args.attempts):
        try:
            result = check(
                args.url, args.expected_version, os.environ["API_KEY"], args.require_backup
            )
            print(json.dumps(result, indent=2))
            return
        except (ValueError, urllib.error.URLError, TimeoutError):
            if attempt + 1 == args.attempts:
                raise SystemExit(
                    "Deployment verification failed; inspect readiness, version, authorization and backup status"
                ) from None
            time.sleep(args.interval)


if __name__ == "__main__":
    main()

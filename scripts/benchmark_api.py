"""Measure HTTP latency, errors, throughput and API resource deltas with real persistence.
Run against an isolated staging workspace: the scored requests become review records.
"""

import argparse
import json
import os
import platform
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--payload", default="src/fraudguard/static/sample.json")
    parser.add_argument("--output", default="artifacts/operations/load_test.json")
    parser.add_argument("--max-p95-ms", type=float, default=500)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    args = parser.parse_args()
    if not 1 <= args.requests <= 100000 or not 1 <= args.concurrency <= 32:
        parser.error("Use 1–100,000 requests and 1–32 concurrent clients")
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8-sig"))
    with httpx.Client(
        base_url=args.url, headers={"X-API-Key": os.environ["API_KEY"]}, timeout=20
    ) as client:
        client.post("/v1/predict", json=payload).raise_for_status()

        def resources():
            response = client.get("/ops/monitoring")
            return response.json()["telemetry"] if response.status_code == 200 else None

        before = resources()

        def send(_):
            start = time.perf_counter()
            try:
                response = client.post("/v1/predict", json=payload)
                status = response.status_code
            except httpx.HTTPError:
                status = 0
            return (time.perf_counter() - start) * 1000, status

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(send, range(args.requests)))
        elapsed = time.perf_counter() - started
        after = resources()
    durations = [r[0] for r in results]
    errors = sum(r[1] != 200 for r in results)
    p95 = float(np.percentile(durations, 95))
    report = {
        "measured_at_epoch": time.time(),
        "client_platform": platform.platform(),
        "requests": args.requests,
        "transactions_per_request": len(payload["transactions"]),
        "concurrency": args.concurrency,
        "seconds": elapsed,
        "requests_per_second": args.requests / elapsed,
        "latency_ms": {str(q): float(np.percentile(durations, q)) for q in (50, 95, 99)},
        "errors": errors,
        "status_counts": dict(Counter(r[1] for r in results)),
        "resources_before": before,
        "resources_after": after,
        "api_cpu_seconds_delta": after["cpu_seconds"] - before["cpu_seconds"]
        if before and after
        else None,
        "gates": {
            "p95_pass": p95 <= args.max_p95_ms,
            "error_rate_pass": errors / args.requests <= args.max_error_rate,
            "max_p95_ms": args.max_p95_ms,
            "max_error_rate": args.max_error_rate,
        },
        "scope": "Single API replica; actual HTTP requests including database writes when STATE_DIR is enabled. Local/staging measurement, not an AWS SLA. Memory snapshots are not peak measurements.",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["gates"]["p95_pass"] and report["gates"]["error_rate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

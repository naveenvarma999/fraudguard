"""Local SQLite transaction saturation test; does not bypass limits on a live API."""

import argparse
import json
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from fraudguard.behavioral_serving import BehavioralRequest, load_bundle
from fraudguard.behavioral_store import ingest, register_model
from fraudguard.store import Store


def run(model_path, output, requests=100):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    bundle = load_bundle(model_path)
    results = []
    for concurrency in (1, 2, 4, 8):
        store = Store(output / f"state-{concurrency}")
        register_model(store, model_path, bundle[1])

        def worker(number):
            latencies, errors = [], []
            for i in range(requests):
                payload = BehavioralRequest(
                    transaction={
                        "event_id": f"{number}-{i}",
                        "account_id": f"load-{number}",
                        "merchant_id": "shop",
                        "timestamp": 1700000000 + i,
                        "amount": float(20 + i % 10),
                        "latitude": 51.0,
                        "longitude": 0.0,
                        "category": "grocery_pos",
                        "home_latitude": 51.0,
                        "home_longitude": 0.0,
                    }
                )
                start = time.perf_counter()
                try:
                    ingest(store, bundle, "load-test", payload)
                    latencies.append(time.perf_counter() - start)
                except Exception as exc:
                    errors.append(type(exc).__name__)
            return latencies, errors

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            groups = list(pool.map(worker, range(concurrency)))
        elapsed = time.perf_counter() - started
        latencies = [value for times, _ in groups for value in times]
        errors = [value for _, errors in groups for value in errors]
        results.append(
            {
                "concurrency": concurrency,
                "successful": len(latencies),
                "errors": errors,
                "seconds": elapsed,
                "transactions_per_second": len(latencies) / elapsed,
                "p50_ms": float(np.quantile(latencies, 0.5) * 1000),
                "p95_ms": float(np.quantile(latencies, 0.95) * 1000),
                "retained_predictions": store.query("SELECT COUNT(*) AS n FROM predictions")[0][
                    "n"
                ],
            }
        )
        print(json.dumps(results[-1]), flush=True)
    report = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "model": bundle[1]["version"],
        "scope": "Direct production ingest transaction, local Windows disk, no HTTP/TLS/auth/quotas, no shadow model; 100 events per writer at every concurrency, identical growing history distribution. Not EC2 capacity or a universal throughput ceiling.",
        "results": results,
        "max_observed_transactions_per_second": max(r["transactions_per_second"] for r in results),
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="artifacts/behavioral_service")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with threadpool_limits(limits=2):
        run(args.model, args.output)

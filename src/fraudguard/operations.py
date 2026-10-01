"""Version-specific drift and delayed-label quality monitoring."""

import json
import os
import shutil
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score


def quality(store, model, days=7):
    cutoff = time.time() - days * 86400
    records = store.query(
        "SELECT p.*,l.fraud,l.at AS labeled_at FROM predictions p LEFT JOIN labels l ON l.prediction_id=p.id WHERE p.model=? AND p.at>=? ORDER BY p.at DESC LIMIT 100001",
        (model, cutoff),
    )
    if len(records) > 100000:
        return {
            "status": "window_too_large",
            "detail": "Reduce the monitoring window; maximum 100,000 predictions",
        }
    labeled = [r for r in records if r["fraud"] is not None]
    count = len(labeled)
    frauds = sum(r["fraud"] for r in labeled)
    result = {
        "predictions": len(records),
        "labeled": count,
        "frauds": frauds,
        "label_coverage": count / len(records) if records else 0,
        "status": "insufficient_labels",
        "window_days": days,
        "cohort": "Predicted within the window; labels available now. Selective and delayed labels can bias metrics.",
    }
    if count < 50 or frauds < 5 or count - frauds < 5:
        return result
    tp = sum(r["fraud"] == 1 and r["decision"] == "review" for r in labeled)
    fp = sum(r["fraud"] == 0 and r["decision"] == "review" for r in labeled)
    fn = frauds - tp
    result.update(
        status="measured",
        recall=tp / frauds,
        precision=tp / (tp + fp) if tp + fp else None,
        average_precision=float(
            average_precision_score(
                [r["fraud"] for r in labeled], [r["probability"] for r in labeled]
            )
        ),
        roc_auc=float(
            roc_auc_score([r["fraud"] for r in labeled], [r["probability"] for r in labeled])
        ),
        confusion={"tp": tp, "fp": fp, "fn": fn, "tn": count - frauds - fp},
        median_label_delay_hours=float(
            np.median([(r["labeled_at"] - r["at"]) / 3600 for r in labeled])
        ),
    )
    return result


def drift(store, manifest, days=7):
    rows = store.query(
        "SELECT feature,counts FROM bins WHERE model=? AND hour>=?",
        (manifest["run_id"], int((time.time() - days * 86400) // 3600)),
    )
    totals = {}
    for row in rows:
        counts = np.array(json.loads(row["counts"]), float)
        totals[row["feature"]] = totals.get(row["feature"], np.zeros_like(counts)) + counts
    size = int(next(iter(totals.values())).sum()) if totals else 0
    result = {
        "status": "measured" if size >= 1000 else "insufficient_samples",
        "rows": size,
        "window_days": days,
        "minimum_rows": 1000,
        "threshold": 0.2,
        "features": {},
        "max_psi": None,
        "interpretation": "PSI is a distribution-shift heuristic, not proof of model quality loss.",
    }
    if size < 1000:
        return result
    for name, counts in totals.items():
        expected = np.array(manifest["reference"][name]["counts"], float) + 0.5
        observed = counts + 0.5
        expected /= expected.sum()
        observed /= observed.sum()
        psi = float(np.sum((observed - expected) * np.log(observed / expected)))
        result["features"][name] = {"psi": psi, "investigate": psi >= 0.2}
    result["max_psi"] = max(f["psi"] for f in result["features"].values())
    return result


class Telemetry:
    def __init__(self):
        self.events = deque(maxlen=100000)
        self.lock = threading.Lock()
        self.started = time.time()

    def record(self, status, elapsed):
        with self.lock:
            self.events.append((time.time(), status, elapsed))

    def snapshot(self, directory):
        with self.lock:
            events = [r for r in self.events if r[0] >= time.time() - 300]
        rss = None
        try:
            pages = int(Path("/proc/self/statm").read_text().split()[1])
            rss = pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, AttributeError):
            pass
        if rss is None and os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t)
                    for name in (
                        "peak",
                        "working",
                        "peak_pool",
                        "pool",
                        "peak_nonpaged",
                        "nonpaged",
                        "pagefile",
                        "peak_pagefile",
                    )
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(Counters),
                wintypes.DWORD,
            ]
            if psapi.GetProcessMemoryInfo(
                kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
            ):
                rss = counters.working
        current = maximum = None
        try:
            current = int(Path("/sys/fs/cgroup/memory.current").read_text())
            maximum = int(Path("/sys/fs/cgroup/memory.max").read_text())
        except (OSError, ValueError):
            pass
        disk = shutil.disk_usage(directory)
        return {
            "window_seconds": 300,
            "requests": len(events),
            "errors_5xx": sum(e[1] >= 500 for e in events),
            "error_rate": sum(e[1] >= 500 for e in events) / len(events) if events else 0,
            "p95_seconds": float(np.quantile([e[2] for e in events], 0.95)) if events else None,
            "uptime_seconds": time.time() - self.started,
            "cpu_seconds": time.process_time(),
            "rss_bytes": rss,
            "container_memory_bytes": current,
            "container_memory_limit": maximum,
            "memory_ratio": current / maximum if current is not None and maximum else None,
            "disk_free_ratio": disk.free / disk.total,
            "disk_free_bytes": disk.free,
            "note": "Latency covers prediction HTTP requests including validation/auth failures. In-memory window resets on restart.",
        }


def alert_conditions(snapshot):
    if snapshot is None:
        return {"service_unavailable": (True, "API monitor check failed")}
    t, d, q = snapshot["telemetry"], snapshot["drift"], snapshot["quality"]
    return {
        "service_unavailable": (False, "API available"),
        "high_error_rate": (
            t["requests"] >= 20 and t["error_rate"] > 0.02,
            "5xx error rate exceeds 2% with at least 20 requests / 5 minutes",
        ),
        "high_latency": (
            t["requests"] >= 20 and t["p95_seconds"] > 0.5,
            "Prediction p95 exceeds 500 ms with at least 20 requests / 5 minutes",
        ),
        "memory_pressure": (
            t["memory_ratio"] is not None and t["memory_ratio"] > 0.85,
            "Container memory exceeds 85% of its limit",
        ),
        "disk_pressure": (
            t["disk_free_ratio"] < 0.1,
            "Workspace filesystem has less than 10% free space",
        ),
        "feature_drift": (
            d["status"] == "measured" and d["max_psi"] >= 0.2,
            "At least one feature has PSI >= 0.2 over the 7-day window",
        ),
        "low_recall": (
            q["status"] == "measured" and q["recall"] < 0.6,
            "Observed labeled-cohort recall is below 60%; inspect coverage and label bias",
        ),
        "monitoring_window_full": (
            q["status"] == "window_too_large",
            "Monitoring cohort exceeds 100,000 rows; reduce window",
        ),
    }


def update_alerts(store, conditions):
    from fraudguard.notifications import enqueue

    now = time.time()
    with store.connect() as db:
        for name, (firing, detail) in conditions.items():
            old = db.execute("SELECT * FROM alerts WHERE name=?", (name,)).fetchone()
            streak = (
                ((old["streak"] if old and now - old["checked"] < 180 else 0) + 1) if firing else 0
            )
            active = int(bool(firing and ((old and old["active"]) or streak >= 2)))
            since = old["since"] if old and old["active"] == active else now
            db.execute(
                "INSERT INTO alerts VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET active=excluded.active,streak=excluded.streak,since=excluded.since,checked=excluded.checked,detail=excluded.detail",
                (name, active, streak, since, now, detail),
            )
            if (old and old["active"] != active) or (not old and active):
                enqueue(db, name, active, detail, now)
                store.audit(
                    db,
                    "monitor",
                    "alert.firing" if active else "alert.resolved",
                    name,
                    {"detail": detail},
                )
                print(
                    json.dumps(
                        {
                            "event": "alert.firing" if active else "alert.resolved",
                            "name": name,
                            "detail": detail,
                        }
                    ),
                    flush=True,
                )

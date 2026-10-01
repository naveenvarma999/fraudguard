import json
import time

import numpy as np

from fraudguard.operations import alert_conditions, drift, quality, update_alerts
from fraudguard.store import Store


def test_distribution_shift_and_delayed_label_quality(bundle, frame, tmp_path):
    store = Store(tmp_path / "state")
    manifest = json.loads((bundle[0] / "manifest.json").read_text())
    rows = frame.iloc[:1000].drop(columns=["Time", "Class"]).to_dict("records")
    for i, row in enumerate(rows):
        row["transaction_id"] = str(i)
        row["V1"] = 100.0
    scores = np.array([0.9 if i % 10 == 0 else 0.01 for i in range(1000)])
    ids = store.record("analyst", rows, scores, manifest)
    shifted = drift(store, manifest)
    assert shifted["status"] == "measured"
    assert shifted["features"]["V1"]["psi"] > 0.2
    assert quality(store, manifest["run_id"])["status"] == "insufficient_labels"
    with store.connect() as db:
        for i, pid in enumerate(ids[:100]):
            db.execute(
                "INSERT INTO labels VALUES(?,?,?,?,?)",
                (pid, int(i % 10 == 0), "test evidence", "analyst", time.time() + 3600),
            )
    observed = quality(store, manifest["run_id"])
    assert observed["recall"] == 1.0 and observed["precision"] == 1.0
    assert observed["label_coverage"] == 0.1
    assert observed["median_label_delay_hours"] >= 1
    assert quality(store, "different-model")["predictions"] == 0
    with store.connect() as db:
        db.execute("UPDATE predictions SET at=?", (time.time() - 10 * 86400,))
        db.execute("UPDATE bins SET hour=?", (int(time.time() // 3600) - 10 * 24,))
    assert drift(store, manifest)["status"] == "insufficient_samples"
    assert quality(store, manifest["run_id"])["labeled"] == 0


def test_insufficient_evidence_and_alert_lifecycle(tmp_path):
    store = Store(tmp_path / "state")
    snapshot = {
        "telemetry": {
            "requests": 20,
            "error_rate": 0.1,
            "p95_seconds": 0.8,
            "memory_ratio": 0.9,
            "disk_free_ratio": 0.05,
        },
        "drift": {"status": "insufficient_samples", "max_psi": None},
        "quality": {"status": "insufficient_labels"},
    }
    conditions = alert_conditions(snapshot)
    assert not conditions["feature_drift"][0] and not conditions["low_recall"][0]
    update_alerts(store, conditions)
    assert not any(a["active"] for a in store.query("SELECT * FROM alerts"))
    update_alerts(store, conditions)
    assert len(store.query("SELECT * FROM alerts WHERE active=1")) == 4
    update_alerts(store, {"high_latency": (False, "Latency recovered")})
    assert store.query("SELECT * FROM audit WHERE action='alert.resolved'")
    update_alerts(store, alert_conditions(None))
    update_alerts(store, alert_conditions(None))
    assert (
        store.query("SELECT active FROM alerts WHERE name='service_unavailable'")[0]["active"] == 1
    )

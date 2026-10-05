import json
import time
from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from fraudguard.auth import add_user
from fraudguard.behavioral import Event
from fraudguard.behavioral_lifecycle import (
    active,
    evidence,
    promote,
    rollback,
    start_shadow,
    submit,
)
from fraudguard.behavioral_serving import BehavioralRequest, load_bundle, save_bundle
from fraudguard.behavioral_store import ingest, register_model
from fraudguard.enriched import ENRICHED, context, delayed_features, known_merchant
from fraudguard.store import Store


@pytest.fixture
def lifecycle(tmp_path):
    store = Store(tmp_path / "state")
    for name in ("trainer", "approver"):
        add_user(store, name, "sufficient-test-password", "admin")
    rng = np.random.default_rng(42)
    x = pd.DataFrame(rng.normal(size=(100, len(ENRICHED))), columns=ENRICHED)
    model = LogisticRegression().fit(x, np.arange(100) % 2)
    path = tmp_path / "model"
    save_bundle(
        path,
        model,
        {
            "threshold": 0.5,
            "dataset": "test",
            "dataset_sha256": "test",
            "seed": 42,
            "selected_model": "logistic",
            "training_asof": 1,
        },
    )
    bundle = load_bundle(path)
    register_model(store, path, bundle[1])
    return store, path, bundle


def event(identifier, timestamp=1000):
    return BehavioralRequest(
        transaction=asdict(Event(identifier, "account", "shop", timestamp, 30.0, 51.0, 0.0))
    )


def test_cross_submitter_and_late_history(lifecycle):
    store, _, bundle = lifecycle
    a = ingest(store, bundle, "service", event("a", 1000))
    b = ingest(store, bundle, "analyst", event("b", 1100))
    assert b["features"]["transactions_10m"] == 1
    late = ingest(store, bundle, "analyst", event("late", 1050))
    assert late["status"] == "stored_late"
    assert ingest(store, bundle, "service", event("late", 1050)) == late
    assert ingest(store, bundle, "service", event("a", 1000)) == a
    c = ingest(store, bundle, "service", event("c", 1200))
    assert c["features"]["transactions_10m"] == 3
    assert len(store.query("SELECT * FROM predictions")) == 3


def test_delayed_merchant_labels_and_corrections(lifecycle):
    store, _, _ = lifecycle
    with store.connect() as db:
        db.execute(
            "INSERT INTO label_history(prediction_id,merchant,event_timestamp,fraud,available_at) VALUES('a','shop',100,1,200)"
        )
        db.execute(
            "INSERT INTO label_history(prediction_id,merchant,event_timestamp,fraud,available_at) VALUES('a','shop',100,0,300)"
        )
        assert known_merchant(db, "shop", 200)["merchant_label_count"] == 0
        assert known_merchant(db, "shop", 201)["merchant_fraud_rate"] == pytest.approx(1.2 / 21)
        assert known_merchant(db, "shop", 301)["merchant_fraud_rate"] == pytest.approx(0.2 / 21)
    frame = pd.DataFrame(
        [
            {"timestamp": 100, "merchant_id": "shop", "label": 1, "label_available_at": 200},
            {"timestamp": 200, "merchant_id": "shop", "label": 0, "label_available_at": 400},
            {"timestamp": 201, "merchant_id": "shop", "label": 0, "label_available_at": 401},
        ]
    )
    values = delayed_features(frame, [True, False, False])
    assert [v["merchant_label_count"] for v in values] == [0, 0, 1]
    assert (
        context(
            {
                "timestamp": 0,
                "latitude": 51.0,
                "longitude": 0.0,
                "home_latitude": 51.0,
                "home_longitude": 0.0,
                "category": "travel",
            }
        )["home_distance_km"]
        == 0
    )


def test_shadow_approval_rollback_and_restart(lifecycle):
    store, path, bundle = lifecycle
    version = submit(store, path, "trainer")
    start_shadow(store, version, "trainer")
    with pytest.raises(ValueError, match="quality gates"):
        promote(store, version, "approver")
    # Distinct live version, with identical feature contract for this controlled fixture.
    live = (bundle[0], {**bundle[1], "version": "live-test"})
    for i in range(60):
        result = ingest(store, live, "service", event(str(i), 1000 + i))
        y = int(i % 3 == 0)
        with store.connect() as db:
            db.execute(
                "INSERT INTO labels VALUES(?,?,?,?,?)",
                (result["prediction_id"], y, "synthetic test", "trainer", time.time()),
            )
            # Controlled paired outcomes to verify the gates independently of model training.
            db.execute(
                "UPDATE predictions SET probability=? WHERE id=?",
                (0.1 if y else 0.9, result["prediction_id"]),
            )
            db.execute(
                "UPDATE shadow_scores SET score=? WHERE prediction_id=?",
                (0.9 if y else 0.1, result["prediction_id"]),
            )
    assert evidence(store, version)["promotable"]
    with pytest.raises(ValueError, match="different"):
        promote(store, version, "trainer")
    promote(store, version, "approver")
    assert active(Store(store.directory), live)[1]["version"] == version
    rollback(store, "approver")
    assert active(store, live)[1]["version"] == "live-test"


def test_shadow_failure_does_not_drop_live_prediction(lifecycle, monkeypatch):
    import fraudguard.behavioral_lifecycle as lifecycle_module

    store, path, bundle = lifecycle
    version = submit(store, path, "trainer")
    start_shadow(store, version, "trainer")

    def fail(*args):
        raise ValueError("Injected shadow artifact failure")

    monkeypatch.setattr(lifecycle_module, "saved_bundle", fail)
    result = ingest(
        store, (bundle[0], {**bundle[1], "version": "live-test"}), "service", event("live")
    )
    assert result["prediction_id"]
    assert len(store.query("SELECT * FROM predictions")) == 1
    assert store.query("SELECT error FROM shadow_scores")[0]["error"] == "ValueError"
    assert not evidence(store, version)["promotable"]


def test_legacy_migration_keeps_predictions_and_rejects_conflicts(tmp_path):
    store = Store(tmp_path / "legacy")
    raw = json.dumps(asdict(Event("a", "account", "shop", 100, 10.0, 51.0, 0.0)))
    with store.connect() as db:
        db.execute("DELETE FROM settings WHERE key='shared_history_v1'")
        for owner in ("one", "two"):
            db.execute(
                "INSERT INTO predictions(id,transaction_id,owner,at,model,amount,probability,threshold,decision) VALUES(?,?,?,?,?,?,?,?,?)",
                (owner, "a", owner, 1.0, "old", 10.0, 0.1, 0.5, "pass"),
            )
            db.execute(
                "INSERT INTO behavioral_events VALUES(?,?,?,?,?,?,?)",
                (owner, "a", "account", 100, raw, "{}", owner),
            )
    restored = Store(store.directory)
    assert len(restored.query("SELECT * FROM account_events")) == 1
    assert len(restored.query("SELECT * FROM predictions")) == 2
    with store.connect() as db:
        db.execute("DELETE FROM settings WHERE key='shared_history_v1'")
        db.execute(
            "UPDATE behavioral_events SET raw=? WHERE owner='two'", (raw.replace("10.0", "11.0"),)
        )
    with pytest.raises(ValueError, match="Conflicting legacy"):
        Store(store.directory)


def test_retrain_uses_only_arrived_labels_and_saves_candidate(lifecycle, tmp_path):
    from fraudguard.retrain import run

    store, _, bundle = lifecycle
    with pytest.raises(ValueError, match="500"):
        run(store, tmp_path / "empty")
    for i in range(500):
        result = ingest(store, bundle, "service", event(str(i), 1000 + i))
        with store.connect() as db:
            db.execute(
                "UPDATE predictions SET at=? WHERE id=?", (1000 + i * 10, result["prediction_id"])
            )
            db.execute(
                "INSERT INTO label_history(prediction_id,merchant,event_timestamp,fraud,available_at) VALUES(?,?,?,?,?)",
                (result["prediction_id"], "shop", 1000 + i, int(i % 5 == 0), 1001 + i * 10),
            )
    with pytest.raises(ValueError, match="500"):
        run(store, tmp_path / "early", asof=2000)
    report = run(store, tmp_path / "candidate", asof=7000)
    assert report["rows"] == 500
    assert report["training_asof"] == 7000
    assert load_bundle(tmp_path / "candidate/model")[1]["schema"] == "behavioral-v2"

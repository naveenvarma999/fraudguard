import pytest
from fastapi.testclient import TestClient

from fraudguard.api import create_app
from fraudguard.auth import add_user
from fraudguard.store import Store

KEY = "workspace-ui-test-service-key"
PASSWORD = "workspace-ui-test-password"


@pytest.fixture
def ui(bundle, tmp_path):
    store = Store(tmp_path / "state")
    for name, role in [("alice", "analyst"), ("bob", "analyst"), ("admin", "admin")]:
        add_user(store, name, PASSWORD, role)
    with TestClient(create_app(bundle[0], KEY, store.directory)) as client:
        headers = {
            name: {
                "Authorization": "Bearer "
                + client.post("/auth/login", json={"username": name, "password": PASSWORD}).json()[
                    "access_token"
                ]
            }
            for name in ["alice", "bob", "admin"]
        }
        yield client, store, headers


def test_overview_scope_window_backlog_and_zero_days(ui, payload):
    client, store, h = ui
    alice = client.post("/v1/predict", headers=h["alice"], json=payload).json()["predictions"][0][
        "prediction_id"
    ]
    bob = client.post("/v1/predict", headers=h["bob"], json=payload).json()["predictions"][0][
        "prediction_id"
    ]
    with store.connect() as db:
        db.execute("UPDATE predictions SET decision='review',review='open' WHERE id=?", (alice,))
        db.execute("UPDATE predictions SET at=0 WHERE id=?", (bob,))
    result = client.get("/ops/overview", headers=h["alice"]).json()
    assert result["transactions"] == 1 and result["flagged"] == 1 and result["backlog"] == 1
    assert len(result["daily"]) == 7 and sum(r["transactions"] for r in result["daily"]) == 1
    assert {r["id"] for r in result["recent"]} == {alice}
    assert client.get("/ops/overview", headers=h["bob"]).json()["transactions"] == 0
    assert len(client.get("/ops/overview", headers=h["admin"]).json()["recent"]) == 2
    assert client.get("/ops/overview", headers={"X-API-Key": KEY}).status_code == 401


def test_details_ownership_and_immutable_correction_history(ui, payload):
    client, _, h = ui
    pid = client.post("/v1/predict", headers=h["alice"], json=payload).json()["predictions"][0][
        "prediction_id"
    ]
    url = "/ops/predictions/" + pid
    assert client.get(url, headers=h["bob"]).status_code == 404
    assert client.get(url).status_code == 401
    client.post(
        url + "/review",
        headers=h["admin"],
        json={"disposition": "cleared", "note": "Reviewed evidence carefully."},
    )
    client.post(
        url + "/label", headers=h["admin"], json={"fraud": 1, "source": "case-123 verified"}
    )
    client.post(
        url + "/label", headers=h["alice"], json={"fraud": 0, "source": "case-123 corrected"}
    )
    data = client.get(url, headers=h["alice"]).json()
    assert data["label"]["fraud"] == 0
    assert [e["action"] for e in data["events"]] == [
        "label.corrected",
        "label.recorded",
        "prediction.reviewed",
    ]
    assert data["prediction"]["note"] == "Reviewed evidence carefully."
    assert client.get("/ops/overview", headers=h["alice"]).json()["backlog"] == 0


def test_detail_event_cursor_is_complete_without_duplicates(ui, payload):
    client, store, h = ui
    pid = client.post("/v1/predict", headers=h["alice"], json=payload).json()["predictions"][0][
        "prediction_id"
    ]
    with store.connect() as db:
        for i in range(55):
            store.audit(
                db, "alice", "prediction.reviewed", pid, {"disposition": "open", "note": str(i)}
            )
    first = client.get("/ops/predictions/" + pid, headers=h["alice"]).json()
    second = client.get(
        "/ops/predictions/" + pid + f"?before={first['next_before']}", headers=h["alice"]
    ).json()
    assert (
        len(first["events"]) == 50 and len(second["events"]) == 5 and second["next_before"] is None
    )
    assert len({e["id"] for e in first["events"] + second["events"]}) == 55

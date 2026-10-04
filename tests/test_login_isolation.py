import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from fraudguard import auth
from fraudguard.api import create_app
from fraudguard.server import trusted_proxies
from fraudguard.store import Store

PASSWORD = "correct-long-test-password"


def test_attack_on_admin_does_not_block_another_source(tmp_path, monkeypatch):
    store = Store(tmp_path)
    auth.add_user(store, "admin", PASSWORD, "admin")
    clock = [1000.0]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    for _ in range(5):
        with pytest.raises(HTTPException) as error:
            auth.login(store, "admin", "wrong", "198.51.100.1")
        assert error.value.status_code == 401
    with pytest.raises(HTTPException) as error:
        auth.login(store, "admin", "wrong", "198.51.100.1")
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "1"
    assert auth.login(store, "admin", PASSWORD, "198.51.100.2")["user"]["name"] == "admin"
    clock[0] += 1.1
    assert auth.login(store, "admin", PASSWORD, "198.51.100.1")["user"]["name"] == "admin"


def test_proxy_clients_are_isolated_and_direct_spoofing_is_ignored(bundle, tmp_path):
    app = create_app(bundle[0], "test-secret-long-enough", tmp_path)
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts="172.30.80.2")
    with TestClient(wrapped, client=("172.30.80.2", 1234)) as client:
        auth.add_user(app.state.store, "admin", PASSWORD, "admin")
        for n in range(30):
            response = client.post(
                "/auth/login",
                json={"username": f"missing{n}", "password": "wrong"},
                headers={"X-Forwarded-For": "198.51.100.1"},
            )
            assert response.status_code == 401
        assert (
            client.post(
                "/auth/login",
                json={"username": "admin", "password": PASSWORD},
                headers={"X-Forwarded-For": "198.51.100.2"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/auth/login",
                json={"username": "admin", "password": PASSWORD},
                headers={"X-Forwarded-For": "198.51.100.1"},
            ).status_code
            == 429
        )
    with TestClient(wrapped, client=("198.51.100.1", 1234)) as client:
        # A direct attacker cannot evade their persisted limit using a fake header.
        assert (
            client.post(
                "/auth/login",
                json={"username": "admin", "password": PASSWORD},
                headers={"X-Forwarded-For": "198.51.100.99"},
            ).status_code
            == 429
        )


@pytest.mark.parametrize("value", ["*", "172.16.0.0/12", "caddy", "127.0.0.1,*"])
def test_proxy_trust_rejects_wildcards_networks_and_names(value):
    with pytest.raises(ValueError):
        trusted_proxies(value)

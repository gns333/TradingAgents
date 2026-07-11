from pathlib import Path

from fastapi.testclient import TestClient

from tradingagents.web import api
from tradingagents.web.admin_store import AdminStore


def test_admin_api_initializes_login_and_saves_masked_model_config(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    client = TestClient(api.create_app())

    assert client.get("/api/admin/status").json()["password_configured"] is False
    assert client.post("/api/admin/setup", json={"password": "correct-horse"}).status_code == 200

    login = client.post("/api/admin/login", json={"password": "correct-horse"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    whitelist = client.post(
        "/api/admin/whitelist",
        headers=headers,
        json={"email": "a@example.com"},
    )
    assert whitelist.status_code == 200

    model = client.post(
        "/api/admin/model-configs",
        headers=headers,
        json={
            "provider": "deepseek",
            "display_name": "DeepSeek",
            "quick_model": "deepseek-v4-flash",
            "deep_model": "deepseek-v4-pro",
            "api_key": "sk-live-secret",
            "is_default": True,
        },
    )
    assert model.status_code == 200
    assert "sk-live-secret" not in str(model.json())

    models = client.get("/api/admin/model-configs", headers=headers).json()
    assert "sk-live-secret" not in str(models)
    assert models["items"][0]["api_key_masked"] == "sk-l****cret"


def test_admin_status_reports_session_validity(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    client = TestClient(api.create_app())
    token = store.create_admin_session()

    anonymous = client.get("/api/admin/status")
    assert anonymous.status_code == 200
    assert anonymous.json()["password_configured"] is True
    assert anonymous.json()["session_valid"] is False

    with_header = client.get(
        "/api/admin/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert with_header.status_code == 200
    assert with_header.json()["session_valid"] is True

    with_cookie = client.get("/api/admin/status", cookies={"ta_admin": token})
    assert with_cookie.status_code == 200
    assert with_cookie.json()["session_valid"] is True

    stale = client.get(
        "/api/admin/status",
        headers={"Authorization": "Bearer stale-token"},
    )
    assert stale.status_code == 200
    assert stale.json()["session_valid"] is False


def test_admin_login_cookie_allows_event_stream_without_whitelist(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    client = TestClient(api.create_app())

    login = client.post("/api/admin/login", json={"password": "correct-horse"})
    assert login.status_code == 200
    assert "ta_admin=" in login.headers["set-cookie"]

    response = client.get(
        "/api/events",
        params={
            "ticker": "600519.SH",
            "trade_date": "2026-07-08",
            "analysts": "market",
        },
    )

    assert response.status_code == 200
    assert "AccessDenied" not in response.text


def test_admin_status_syncs_valid_header_session_to_cookie(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    client = TestClient(api.create_app())
    token = store.create_admin_session()

    response = client.get(
        "/api/admin/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["session_valid"] is True
    assert "ta_admin=" in response.headers["set-cookie"]


def test_events_require_whitelisted_identity_after_admin_setup(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    client = TestClient(api.create_app())

    response = client.get(
        "/api/events",
        params={
            "ticker": "600519.SH",
            "trade_date": "2026-07-08",
            "analysts": "market",
        },
    )

    assert response.status_code == 200
    assert "AccessDenied" in response.text
    assert "白名单" in response.text


def test_events_allow_admin_without_whitelist(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    client = TestClient(api.create_app())
    token = store.create_admin_session()

    response = client.get(
        "/api/events",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "ticker": "600519.SH",
            "trade_date": "2026-07-08",
            "analysts": "market",
        },
    )

    assert response.status_code == 200
    assert "AccessDenied" not in response.text


def test_sector_screen_api_is_removed():
    client = TestClient(api.create_app())

    paths = {route.path for route in client.app.routes}

    assert "/api/sector/screen" not in paths

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

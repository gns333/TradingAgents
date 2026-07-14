from pathlib import Path

from fastapi.testclient import TestClient

from tradingagents.web import api
from tradingagents.web.admin_store import AdminStore
from tradingagents.web.model_catalog import ModelInfo
from tradingagents.web.events import AnalysisEvent
from tradingagents.web.task_service import AnalysisTaskService


class FakeTaskService:
    def submit(self, run_id):
        return None

    def recover(self):
        return []

    def shutdown(self, wait=True):
        return None


class FakeModelCatalog:
    def __init__(self):
        self.calls = []

    def fetch(self, provider, api_key, base_url=None):
        self.calls.append((provider, api_key, base_url))
        return [ModelInfo("same-model", "Same Model")]


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


def test_legacy_connection_bound_event_stream_is_removed():
    client = TestClient(api.create_app())

    paths = {route.path for route in client.app.routes}

    assert "/api/events" not in paths


def test_sector_screen_api_is_removed():
    client = TestClient(api.create_app())

    paths = {route.path for route in client.app.routes}

    assert "/api/sector/screen" not in paths


def test_create_run_returns_409_with_existing_run(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    store.upsert_whitelist({"email": "user@example.com", "status": "active"})
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    monkeypatch.setattr(api, "create_task_service", lambda current_store: FakeTaskService(), raising=False)
    client = TestClient(api.create_app())
    payload = {
        "ticker": "600519.SH",
        "stock_name": "贵州茅台",
        "trade_date": "2026-07-14",
        "analysts": ["market"],
    }

    first = client.post(
        "/api/runs",
        params={"access_email": "user@example.com"},
        json=payload,
    )
    second = client.post(
        "/api/runs",
        params={"access_email": "user@example.com"},
        json=payload,
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["error_type"] == "ActiveRunExists"
    assert second.json()["detail"]["run"]["id"] == first.json()["run"]["id"]
    active = client.get(
        "/api/runs/active",
        params={"access_email": "user@example.com"},
    )
    assert active.json()["run"]["id"] == first.json()["run"]["id"]


def test_create_run_requires_identity_after_admin_setup(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    monkeypatch.setattr(api, "create_task_service", lambda current_store: FakeTaskService(), raising=False)
    client = TestClient(api.create_app())

    response = client.post(
        "/api/runs",
        json={
            "ticker": "600519.SH",
            "trade_date": "2026-07-14",
            "analysts": ["market"],
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"]["error_type"] == "IdentityRequired"


def test_admin_model_catalog_uses_saved_key_without_echoing_it(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    config = store.save_model_config(
        {
            "provider": "deepseek",
            "display_name": "DeepSeek",
            "quick_model": "same-model",
            "deep_model": "same-model",
            "api_key": "sk-live-secret",
            "is_default": True,
        }
    )
    catalog = FakeModelCatalog()
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    monkeypatch.setattr(api, "get_model_catalog", lambda: catalog, raising=False)
    client = TestClient(api.create_app())
    token = store.create_admin_session()

    response = client.post(
        "/api/admin/model-catalog",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider": "deepseek", "config_id": config["id"]},
    )

    assert response.status_code == 200
    assert response.json()["models"] == [
        {"id": "same-model", "display_name": "Same Model"}
    ]
    assert "sk-live-secret" not in response.text
    assert catalog.calls == [("deepseek", "sk-live-secret", None)]


def test_background_run_completes_and_archives_without_opening_stream(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    events = [
        AnalysisEvent("run_started", {}),
        AnalysisEvent(
            "report_section_updated",
            {"section": "market_report", "content": "# 市场报告\n\n内容"},
        ),
        AnalysisEvent("run_completed", {"final_state": {}}),
    ]
    service = AnalysisTaskService(
        store,
        graph_builder=lambda request: object(),
        event_stream=lambda graph, request: events,
    )
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    monkeypatch.setattr(api, "create_task_service", lambda current_store: service)
    client = TestClient(api.create_app())

    response = client.post(
        "/api/runs",
        json={
            "ticker": "600519.SH",
            "stock_name": "贵州茅台",
            "trade_date": "2026-07-14",
            "analysts": ["market"],
        },
    )
    run_id = response.json()["run"]["id"]
    service.submit(run_id).result(timeout=2)

    run = client.get(f"/api/runs/{run_id}").json()["run"]
    assert run["status"] == "completed"
    assert run["stock_name"] == "贵州茅台"
    assert client.get("/api/runs/active").json()["run"] is None
    persisted = client.get(f"/api/runs/{run_id}/events").json()["items"]
    assert [item["event"] for item in persisted][-1] == "run_completed"
    report = client.get(f"/api/reports/{run['report_id']}").json()["item"]
    assert report["stock_name"] == "贵州茅台"
    service.shutdown()

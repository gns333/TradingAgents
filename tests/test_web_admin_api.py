import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from tradingagents.web import api, stock_directory
from tradingagents.web.admin_store import AdminStore
from tradingagents.web.events import AnalysisEvent
from tradingagents.web.identity import CloudBaseIdentityProvider
from tradingagents.web.model_catalog import ModelInfo
from tradingagents.web.runtime import WebRuntimeConfig
from tradingagents.web.task_service import AnalysisTaskService


class FakeTaskService:
    def __init__(self):
        self.notified = False

    def start(self):
        return None

    def notify(self):
        self.notified = True

    def submit(self, run_id):
        return None

    def recover(self):
        return []

    def shutdown(self, wait=True):
        return None


class FakeTokenVerifier:
    def __init__(self, context):
        self.context = context
        self.headers = None

    def from_headers(self, headers):
        self.headers = headers
        return dict(self.context)


def _cloud_context(uid: str, email: str = "") -> str:
    payload = json.dumps({"uid": uid, "email": email}).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def _cloud_runtime() -> WebRuntimeConfig:
    return WebRuntimeConfig(
        mode="cloudbase",
        database_url="mysql+pymysql://unused",
        cloudbase_env_id="env-123",
        cloudbase_region="ap-shanghai",
        master_key=b"k" * 32,
    )


def _cloud_app(store: AdminStore, **kwargs):
    return api.create_app(
        store=store,
        runtime=_cloud_runtime(),
        identity_provider=CloudBaseIdentityProvider(store),
        **kwargs,
    )


class FakeModelCatalog:
    def __init__(self):
        self.calls = []

    def fetch(self, provider, api_key, base_url=None):
        self.calls.append((provider, api_key, base_url))
        return [ModelInfo("same-model", "Same Model")]


def test_create_run_defaults_to_all_four_analysts(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    client = TestClient(
        api.create_app(store=store, task_service=FakeTaskService())
    )

    response = client.post(
        "/api/runs",
        json={"ticker": "600519.SH", "trade_date": "2026-07-23"},
    )

    assert response.status_code == 201
    assert response.json()["run"]["analysts"] == [
        "market",
        "news",
        "fundamentals",
        "social",
    ]


def test_create_run_rejects_future_trade_date(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    client = TestClient(
        api.create_app(store=store, task_service=FakeTaskService())
    )

    response = client.post(
        "/api/runs",
        json={"ticker": "600519.SH", "trade_date": "2999-01-01"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "分析日期不能晚于今天"
    assert store.get_active_analysis_run("anonymous") is None


def test_create_run_resolves_common_etf_name(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    monkeypatch.setattr(stock_directory, "_load_cached_akshare_entries", lambda: {})
    monkeypatch.setattr(stock_directory, "_load_cached_hk_akshare_entries", lambda: {})
    monkeypatch.setattr(
        stock_directory, "_load_cached_etf_akshare_entries", lambda: {}, raising=False
    )
    directory = stock_directory.StockDirectory()
    monkeypatch.setattr(api, "get_stock_directory", lambda: directory)
    client = TestClient(
        api.create_app(store=store, task_service=FakeTaskService())
    )

    response = client.post(
        "/api/runs",
        json={"ticker": "510300.SH", "trade_date": "2026-07-23"},
    )

    assert response.status_code == 201
    assert response.json()["run"]["stock_name"] == "沪深300ETF"


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


def test_readyz_reports_database_readiness(tmp_path: Path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    client = TestClient(api.create_app(store=store, task_service=FakeTaskService()))

    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/readyz").json() == {"ok": True}

    monkeypatch.setattr(store, "ping", lambda: False, raising=False)
    unavailable = client.get("/readyz")

    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "database unavailable"


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


def test_runtime_config_exposes_only_public_cloudbase_values(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    client = TestClient(_cloud_app(store))

    response = client.get("/api/runtime-config")

    assert response.json() == {
        "runtime": "cloudbase",
        "auth": "cloudbase",
        "env_id": "env-123",
        "region": "ap-shanghai",
        "sdk_url": (
            "https://static.cloudbase.net/"
            "cloudbase-js-sdk/2.28.6/cloudbase.full.js"
        ),
    }
    assert "mysql" not in response.text
    assert "master" not in response.text


def test_cloudbase_admin_can_update_runtime_settings(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.upsert_app_user(
        {"uid": "admin-1", "role": "admin", "status": "active"}
    )
    service = FakeTaskService()
    client = TestClient(
        _cloud_app(store, task_service=service)
    )
    headers = {"x-cloudbase-context": _cloud_context("admin-1")}

    response = client.put(
        "/api/admin/runtime-settings",
        headers=headers,
        json={
            "analysis_concurrency_limit": 4,
            "analysis_queue_limit": 50,
            "accept_new_tasks": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["settings"]["analysis_concurrency_limit"] == 4
    assert service.notified is True


def test_cloudbase_user_cannot_call_admin_api(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.upsert_app_user(
        {"uid": "user-1", "role": "user", "status": "active"}
    )
    client = TestClient(_cloud_app(store))

    response = client.get(
        "/api/admin/runtime-settings",
        headers={"x-cloudbase-context": _cloud_context("user-1")},
    )

    assert response.status_code == 403


def test_cloudbase_session_and_user_admin_endpoints(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.upsert_app_user(
        {
            "uid": "admin-1",
            "email": "admin@example.com",
            "role": "admin",
            "status": "active",
        }
    )
    client = TestClient(_cloud_app(store))
    headers = {"x-cloudbase-context": _cloud_context("admin-1")}

    session = client.get("/api/session", headers=headers)
    assert session.json()["user"]["is_admin"] is True

    created = client.post(
        "/api/admin/users",
        headers=headers,
        json={"uid": "user-2", "role": "user", "status": "active"},
    )
    assert created.status_code == 200
    assert created.json()["item"]["uid"] == "user-2"
    assert client.get("/api/admin/users", headers=headers).status_code == 200


def test_cloudbase_registration_creates_disabled_business_user(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    client = TestClient(_cloud_app(store))

    response = client.post(
        "/api/register",
        headers={
            "x-cloudbase-context": _cloud_context(
                "new-user", "New.User@Example.com"
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["approval_status"] == "pending"
    assert store.get_app_user("new-user") == response.json()["user"]
    assert response.json()["user"]["email"] == "new.user@example.com"
    assert response.json()["user"]["role"] == "user"
    assert response.json()["user"]["status"] == "disabled"


def test_cloudbase_registration_uses_verified_bearer_identity(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    verifier = FakeTokenVerifier(
        {"uid": "verified-user", "email": "verified@example.com"}
    )
    client = TestClient(
        api.create_app(
            store=store,
            runtime=_cloud_runtime(),
            identity_provider=CloudBaseIdentityProvider(store, verifier),
        )
    )

    response = client.post(
        "/api/register",
        headers={"Authorization": "Bearer cloudbase-user-token"},
        json={"email": "forged@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["uid"] == "verified-user"
    assert response.json()["user"]["email"] == "verified@example.com"
    assert verifier.headers["authorization"] == "Bearer cloudbase-user-token"


def test_cloudbase_registration_does_not_overwrite_existing_user(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    original = store.upsert_app_user(
        {
            "uid": "existing-admin",
            "email": "admin@example.com",
            "role": "admin",
            "status": "active",
        }
    )
    client = TestClient(_cloud_app(store))

    response = client.post(
        "/api/register",
        headers={
            "x-cloudbase-context": _cloud_context(
                "existing-admin", "changed@example.com"
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["approval_status"] == "active"
    assert store.get_app_user("existing-admin") == original


def test_registration_requires_cloudbase_runtime_and_context(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    cloud_client = TestClient(_cloud_app(store))
    local_client = TestClient(api.create_app(store=store))

    assert cloud_client.post("/api/register").status_code == 401
    assert local_client.post("/api/register").status_code == 404


def test_cloudbase_mode_hides_local_admin_password_endpoints(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    client = TestClient(_cloud_app(store))

    assert client.post("/api/admin/setup", json={"password": "correct-horse"}).status_code == 404
    assert client.post("/api/admin/login", json={"password": "correct-horse"}).status_code == 404


def test_create_run_returns_429_when_global_queue_is_full(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    store.upsert_whitelist({"email": "a@example.com", "status": "active"})
    store.upsert_whitelist({"email": "b@example.com", "status": "active"})
    store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 1,
            "analysis_queue_limit": 1,
            "accept_new_tasks": True,
        },
        updated_by="admin:local",
    )
    client = TestClient(
        api.create_app(store=store, task_service=FakeTaskService())
    )
    payload = {
        "ticker": "600519.SH",
        "trade_date": "2026-07-16",
        "analysts": ["market"],
    }

    first = client.post(
        "/api/runs",
        params={"access_email": "a@example.com"},
        json=payload,
    )
    second = client.post(
        "/api/runs",
        params={"access_email": "b@example.com"},
        json=payload,
    )

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json()["detail"]["error_type"] == "QueueLimitReached"


def test_create_run_returns_503_when_submissions_are_paused(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 1,
            "analysis_queue_limit": 20,
            "accept_new_tasks": False,
        },
        updated_by="admin:local",
    )
    client = TestClient(
        api.create_app(store=store, task_service=FakeTaskService())
    )

    response = client.post(
        "/api/runs",
        json={
            "ticker": "600519.SH",
            "trade_date": "2026-07-16",
            "analysts": ["market"],
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error_type"] == "TaskSubmissionPaused"

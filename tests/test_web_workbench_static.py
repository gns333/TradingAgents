from pathlib import Path

from fastapi.testclient import TestClient

from tradingagents.web import api


STATIC_DIR = Path("tradingagents/web/static")


def test_workbench_serves_split_static_assets():
    client = TestClient(api.create_app())

    css = client.get("/assets/workbench.css")
    js = client.get("/assets/workbench.js")
    page = client.get("/")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert page.status_code == 200
    assert 'id="app-shell"' in page.text
    assert 'src="/assets/workbench.js"' in page.text


def test_workbench_static_assets_are_not_cached_during_local_development():
    client = TestClient(api.create_app())

    for path in ["/", "/admin", "/assets/workbench.css", "/assets/workbench.js"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "no-store" in response.headers.get("cache-control", "")


def test_admin_route_is_compatibility_bridge_to_workbench_admin():
    client = TestClient(api.create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'data-admin-bridge="true"' in response.text
    assert "/?view=admin" in response.text


def test_workbench_has_required_navigation_and_identity_targets():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert 'data-view="analysis"' in html
    assert 'data-view="reports"' in html
    assert 'data-view="admin"' in html
    assert 'id="identity-email"' in html
    assert 'id="identity-modal"' in html
    assert 'id="open-identity-modal"' in html
    assert "function identityQuery()" in js
    assert "function adminHeaders()" in js
    assert "function refreshAdminStatus()" in js
    assert "status.session_valid" in js
    assert "persistAdminSession('')" in js
    assert "接入正式登录后，将由登录态自动提供身份" in html
    assert "管理员模式下无需邮箱白名单" in js
    assert "function persistAdminSession(token)" in js
    assert "document.cookie = `ta_admin=" in js
    assert "input.reportValidity()" in js
    assert "params.get('identity') === 'edit'" in js
    assert "event.key !== 'Escape'" in js


def test_workbench_no_longer_exposes_sector_screening():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert 'data-view="sector"' not in html
    assert 'id="view-sector"' not in html
    assert "renderSectorWorkspace" not in js
    assert "renderSectorResults" not in js
    assert "/api/sector/screen" not in js


def test_workbench_js_contains_analysis_workspace_contract():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "function renderAnalysisWorkspace()" in js
    assert "function setAnalysisTicker(ticker, name)" in js
    assert "async function restoreActiveRun()" in js
    assert "function attachToRun(run" in js
    assert "/api/runs" in js
    assert "async function pollActiveRun(runId)" in js
    assert "new EventSource" not in js
    assert "实时连接中断" not in js
    assert "run_started" in js
    assert "report_section_updated" in js
    assert "function advancePipelineStage()" in js
    assert "created_at: item.created_at" in js
    assert "function formatEventTime(value)" in js
    assert "function focusCurrentAgent()" in js
    assert "requestAnimationFrame(focusCurrentAgent)" in js


def test_workbench_js_contains_terminal_redesign_contract():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert 'data-theme="dark"' in html
    assert 'id="theme-toggle"' in html
    assert 'id="ticker-bar"' in html
    assert "function applyTheme(theme)" in js
    assert "function classifyDecision(text)" in js
    assert "function decisionBadgeHtml(text)" in js
    assert "function filteredHistory()" in js


def test_workbench_polling_uses_authenticated_api_requests():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "async function startAnalysis()" in js
    poll_start = js.index("async function pollActiveRun(runId)")
    poll_body = js[poll_start : poll_start + 1800]
    assert "loadPersistedRunEvents" in poll_body
    assert "adminHeaders()" in poll_body
    assert "scheduleRunPoll(runId)" in poll_body
    assert "state.pollTimer = setTimeout" in js


def test_workbench_js_contains_report_center_contract():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "function renderMarkdown(markdown)" in js
    assert "function renderReportCenter()" in js
    assert "function renderReportPreview()" in js
    assert "final_trade_decision" in js


def test_workbench_js_contains_ticker_autocomplete_contract():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "function setupTickerAutocomplete()" in js
    assert "/api/stocks/search" in js
    assert "function renderTickerSuggestions(items)" in js
    assert 'role="combobox"' in js


def test_workbench_js_contains_report_history_contract():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "function loadReportHistory()" in js
    assert "function openHistoryReport(id)" in js
    assert "function deleteHistoryReport(id)" in js
    assert "/api/reports" in js
    assert "method: 'DELETE'" in js


def test_workbench_js_contains_provider_preset_contract():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "PROVIDER_PRESETS" in js
    assert "function applyProviderPreset(clearModels = true)" in js
    assert "https://api.deepseek.com" in js
    assert "readOnly" in js
    assert "async function fetchModelCatalog()" in js
    assert "/api/admin/model-catalog" in js
    assert "quick_models:" not in js
    assert "deep_models:" not in js


def test_workbench_hides_empty_admin_error_and_uses_stock_names_in_reports():
    css = (STATIC_DIR / "workbench.css").read_text(encoding="utf-8")
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert ".form-status:empty" in css
    assert "function reportInstrumentLabel(report)" in js
    assert "stock_name" in js


def test_workbench_js_contains_admin_workspace_contract():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "function renderAdminWorkspace()" in js
    assert "function logoutAdmin()" in js
    assert "function loadAdminData()" in js
    assert "/api/admin/whitelist" in js
    assert "/api/admin/model-configs" in js
    assert "api_key" in js
    assert "ta_admin_token" in js
    assert 'id="admin-logout"' in js
    assert "function switchAdminPane(pane)" in js
    assert "function selectModel(id)" in js
    assert "function selectWhitelist(email)" in js
    assert "config_id: state.selectedModelId" in js
    assert "params.get('adminPane')" in js
    assert "query.set('adminPane', state.adminPane)" in js


def test_workbench_supports_cloudbase_runtime_and_access_tokens():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "async function loadRuntimeConfig()" in js
    assert "function loadCloudBaseSdk(url)" in js
    assert "async function restoreCloudBaseSession()" in js
    assert "async function signInCloudBase(username, password)" in js
    assert "Authorization: `Bearer ${state.accessToken}`" in js
    assert "/api/runtime-config" in js
    assert "/api/session" in js


def test_cloudbase_login_ui_exists_without_removing_local_admin_login():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="cloudbase-auth-modal"' in html
    assert 'id="cloudbase-username"' in html
    assert 'id="cloudbase-password"' in html
    assert 'id="admin-modal"' in html


def test_admin_workspace_supports_runtime_settings_and_cloudbase_users():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert 'data-admin-pane="runtime"' in js
    assert 'id="runtime-concurrency"' in js
    assert 'id="runtime-queue-limit"' in js
    assert 'id="runtime-accepting"' in js
    assert "/api/admin/runtime-settings" in js
    assert 'data-admin-pane="users"' in js
    assert 'id="user-role"' in js
    assert 'id="user-status"' in js
    assert "/api/admin/users" in js


def test_workbench_static_files_follow_accessibility_basics():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "workbench.css").read_text(encoding="utf-8")

    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert 'aria-label="主导航"' in html
    assert 'aria-modal="true"' in html
    assert "@media (max-width: 860px)" in css
    assert ":focus-visible" in css

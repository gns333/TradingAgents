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
    html = (STATIC_DIR / "index.html").read_text()
    js = (STATIC_DIR / "workbench.js").read_text()

    assert 'data-view="analysis"' in html
    assert 'data-view="reports"' in html
    assert 'data-view="admin"' in html
    assert 'id="identity-email"' in html
    assert "function identityQuery()" in js
    assert "function adminHeaders()" in js
    assert "function refreshAdminStatus()" in js
    assert "status.session_valid" in js
    assert "persistAdminSession('')" in js
    assert "本地开发管理员登录后无需填写" in html
    assert "管理员模式：本地开发已授权" in js
    assert "function persistAdminSession(token)" in js
    assert "document.cookie = `ta_admin=" in js


def test_workbench_no_longer_exposes_sector_screening():
    html = (STATIC_DIR / "index.html").read_text()
    js = (STATIC_DIR / "workbench.js").read_text()

    assert 'data-view="sector"' not in html
    assert 'id="view-sector"' not in html
    assert "renderSectorWorkspace" not in js
    assert "renderSectorResults" not in js
    assert "/api/sector/screen" not in js


def test_workbench_js_contains_analysis_workspace_contract():
    js = (STATIC_DIR / "workbench.js").read_text()

    assert "function renderAnalysisWorkspace()" in js
    assert "function parseAnalysisEventData(raw)" in js
    assert "parsed.data || {}" in js
    assert "function setAnalysisTicker(ticker)" in js
    assert "new EventSource" in js
    assert "run_started" in js
    assert "report_section_updated" in js


def test_workbench_syncs_admin_session_before_opening_event_stream():
    js = (STATIC_DIR / "workbench.js").read_text()

    assert "async function startAnalysis()" in js
    sync_index = js.index("await refreshAdminStatus()")
    stream_index = js.index("new EventSource")
    assert sync_index < stream_index


def test_workbench_js_contains_report_center_contract():
    js = (STATIC_DIR / "workbench.js").read_text()

    assert "function renderMarkdown(markdown)" in js
    assert "function renderReportCenter()" in js
    assert "function renderReportPreview()" in js
    assert "final_trade_decision" in js


def test_workbench_js_contains_ticker_autocomplete_contract():
    js = (STATIC_DIR / "workbench.js").read_text()

    assert "function setupTickerAutocomplete()" in js
    assert "/api/stocks/search" in js
    assert "function renderTickerSuggestions(items)" in js
    assert 'role="combobox"' in js


def test_workbench_js_contains_report_history_contract():
    js = (STATIC_DIR / "workbench.js").read_text()

    assert "function loadReportHistory()" in js
    assert "function openHistoryReport(id)" in js
    assert "function deleteHistoryReport(id)" in js
    assert "/api/reports" in js
    assert "method: 'DELETE'" in js


def test_workbench_js_contains_provider_preset_contract():
    js = (STATIC_DIR / "workbench.js").read_text()

    assert "PROVIDER_PRESETS" in js
    assert "function applyProviderPreset()" in js
    assert "https://api.deepseek.com" in js
    assert "readOnly" in js
    assert "function fillModelSelect(select, models, allowCustom)" in js


def test_workbench_js_contains_admin_workspace_contract():
    js = (STATIC_DIR / "workbench.js").read_text()

    assert "function renderAdminWorkspace()" in js
    assert "function logoutAdmin()" in js
    assert "function loadAdminData()" in js
    assert "/api/admin/whitelist" in js
    assert "/api/admin/model-configs" in js
    assert "api_key" in js
    assert "ta_admin_token" in js
    assert "退出登录" in js


def test_workbench_static_files_follow_accessibility_basics():
    html = (STATIC_DIR / "index.html").read_text()
    css = (STATIC_DIR / "workbench.css").read_text()

    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert 'aria-label="主导航"' in html
    assert 'aria-modal="true"' in html
    assert "@media (max-width: 860px)" in css
    assert ":focus-visible" in css

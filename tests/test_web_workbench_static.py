import re
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


def test_api_json_authenticates_headerless_cloudbase_requests():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    api_start = js.index("async function apiJson(url, options = {})")
    api_body = js[api_start : api_start + 1000]
    assert "&& options.headers" not in api_body
    assert "...(options.headers || {})" in api_body
    assert "Authorization: `Bearer ${state.accessToken}`" in api_body


def test_start_analysis_prompts_for_cloudbase_login_before_request():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "function hasAnalysisIdentity()" in js
    assert "function showAnalysisLoginPrompt(message)" in js

    start = js.index("async function startAnalysis()")
    body = js[start : start + 2200]
    guard = body.index("if (!hasAnalysisIdentity())")
    reset = body.index("resetRunView()")

    assert guard < reset
    assert "showAnalysisLoginPrompt(" in body
    assert "err.status === 401 && state.runtime.auth === 'cloudbase'" in body
    assert "setCloudBaseAuthMode('login')" in js



def test_workbench_js_contains_report_center_contract():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "function renderMarkdown(markdown)" in js
    assert "function renderReportCenter()" in js
    assert "function renderReportPreview()" in js
    assert "function clearHistorySelection(" in js
    assert "function setHistoryMobileDetail(open)" in js
    assert "history-detail-open" in js
    assert "report-tab-scroll" in js
    assert "scrollIntoView({ behavior: 'smooth', block: 'start' })" in js
    assert "final_trade_decision" in js
    assert "investment_debate_report: '多空辩论'" in js
    assert "risk_debate_report: '风险辩论'" in js
    order_start = js.index("function orderedReportSections()")
    order_body = js[order_start : order_start + 500]
    assert order_body.index("'investment_debate_report'") < order_body.index("'investment_plan'")
    assert order_body.index("'risk_debate_report'") < order_body.index("'final_trade_decision'")


def test_team_progress_includes_debate_stages_in_pipeline_order():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "workbench.css").read_text(encoding="utf-8")

    assert "investment_debate: {" in js
    assert "risk_debate: {" in js
    pipeline_start = js.index("const PIPELINE_ORDER")
    pipeline_body = js[pipeline_start : pipeline_start + 500]
    assert pipeline_body.index("'investment_debate'") < pipeline_body.index(
        "'research_manager'"
    )
    assert pipeline_body.index("'trader'") < pipeline_body.index("'risk_debate'")
    assert pipeline_body.index("'risk_debate'") < pipeline_body.index(
        "'portfolio_manager'"
    )
    assert "investment_debate_report: 'investment_debate'" in js
    assert "risk_debate_report: 'risk_debate'" in js
    assert "'investment_debate', 'research_manager', 'trader', 'risk_debate'" in js
    assert "grid-template-columns: repeat(9, minmax(104px, 1fr));" in css
    assert "min-width: 960px;" in css


def test_debate_reports_render_as_speaker_timeline_cards():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "workbench.css").read_text(encoding="utf-8")

    assert "const DEBATE_REPORT_CONFIG" in js
    assert "function parseDebateTurns(section, markdown)" in js
    assert "function renderDebateTimeline(section, markdown)" in js
    assert "debate-turn" in js
    assert "debate-round" in js
    assert "isDebateReport(section)" in js
    assert "renderDebateTimeline(section, body)" in js
    assert ".debate-timeline" in css
    assert '.debate-turn[data-speaker="bull"]' in css
    assert '.debate-turn[data-speaker="bear"]' in css
    assert '.debate-turn[data-speaker="neutral"]' in css


def test_web_package_includes_nested_vendor_assets():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '"tradingagents.web" = ["static/*", "static/vendor/*"]' in pyproject

def test_reports_use_local_markdown_it_and_dompurify():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")
    markdown_it = STATIC_DIR / "vendor" / "markdown-it.min.js"
    dompurify = STATIC_DIR / "vendor" / "purify.min.js"

    markdown_script = '<script src="/assets/vendor/markdown-it.min.js" defer></script>'
    purify_script = '<script src="/assets/vendor/purify.min.js" defer></script>'
    workbench_script = '<script src="/assets/workbench.js" defer></script>'
    assert markdown_script in html
    assert purify_script in html
    assert html.index(markdown_script) < html.index(purify_script)
    assert html.index(purify_script) < html.index(workbench_script)
    assert markdown_it.exists() and markdown_it.stat().st_size > 10_000
    assert dompurify.exists() and dompurify.stat().st_size > 10_000
    assert "window.markdownit" in js
    assert "window.DOMPurify" in js
    assert "DOMPurify.sanitize(markdownRenderer.render" in js
    assert "function renderInline(value)" not in js


def test_report_markdown_theme_has_readability_and_mobile_table_rules():
    css = (STATIC_DIR / "workbench.css").read_text(encoding="utf-8")

    assert ".markdown h2::before" in css
    assert ".markdown table" in css
    assert "display: block;" in css
    assert "overflow-x: auto;" in css
    assert ".markdown tbody tr:nth-child(even)" in css
    assert ".markdown blockquote" in css
    assert ".debate-turn-body.markdown" in css


def test_report_center_exposes_unrated_badge_and_filter():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "workbench.css").read_text(encoding="utf-8")

    assert "unrated: { kind: 'unrated', label: '未评级' }" in js
    assert 'data-filter="unrated">未评级</button>' in js
    assert ".decision-badge.unrated" in css


def test_complete_analysis_enables_sentiment_by_default():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert re.search(
        r'<input\s+type="checkbox"\s+name="analyst"\s+value="social"\s+checked>',
        js,
    )


def test_workbench_js_contains_ticker_autocomplete_contract():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "function setupTickerAutocomplete()" in js
    assert "/api/stocks/search" in js
    assert "function renderTickerSuggestions(items)" in js
    assert 'role="combobox"' in js


def test_trade_date_input_can_shrink_on_mobile_safari():
    css = (STATIC_DIR / "workbench.css").read_text(encoding="utf-8")

    date_rules = re.findall(r"#trade-date\s*\{([^}]*)\}", css)
    assert date_rules
    date_rule = date_rules[-1]
    assert "min-width: 0;" in date_rule
    assert "min-inline-size: 0;" in date_rule
    assert "max-width: 100%;" in date_rule


def test_workbench_js_contains_report_history_contract():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "function loadReportHistory()" in js
    assert "function openHistoryReport(id)" in js
    assert "function deleteHistoryReport(id)" in js
    assert "/api/reports" in js
    assert "method: 'DELETE'" in js


def test_report_history_card_reserves_a_separate_delete_column():
    css = (STATIC_DIR / "workbench.css").read_text(encoding="utf-8")

    assert ".history-item { display: grid; grid-template-columns: minmax(0, 1fr) 44px; }" in css
    assert ".history-open { min-width: 0; width: 100%; max-width: 100%;" in css
    assert ".history-open .ho-owner {" in css
    assert "text-overflow: ellipsis;" in css
    assert "#reports-root.history-detail-open .history-panel { display: none; }" in css

    delete_rules = re.findall(r"\.history-delete\s*\{([^}]*)\}", css)
    assert delete_rules
    assert any("position: static;" in rule and "transform: none;" in rule for rule in delete_rules)
    assert all("position: absolute;" not in rule for rule in delete_rules)


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
    assert "Authorization: `Bearer ${state.accessToken}`" in js
    assert "/api/runtime-config" in js
    assert "/api/session" in js
    assert "accessKey: state.runtime.publishable_key" not in js


def test_cloudbase_password_login_ui_exists_without_removing_local_admin_login():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="cloudbase-auth-modal"' in html
    assert 'id="cloudbase-login-email"' in html
    assert 'id="cloudbase-login-password"' in html
    assert 'autocomplete="current-password"' in html
    assert 'id="cloudbase-login-code"' not in html
    assert 'id="cloudbase-send-login-code"' not in html
    assert 'id="admin-modal"' in html


def test_cloudbase_email_registration_ui_and_sdk_flow_exist():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert 'id="show-cloudbase-login"' in html
    assert 'id="show-cloudbase-register"' in html
    assert 'id="cloudbase-register-email"' in html
    assert 'id="cloudbase-register-code"' in html
    assert 'id="cloudbase-register-password"' in html
    assert 'id="cloudbase-register-confirm"' in html
    assert 'id="cloudbase-send-code"' in html
    assert 'id="cloudbase-sign-up"' in html
    assert "async function requestCloudBaseRegistrationCode(email, password)" in js
    assert "async function verifyCloudBaseRegistrationCode(email, code)" in js
    assert "state.cloudbaseAuth.signUp({ email, password })" in js
    assert "challenge.verifyOtp" in js
    assert "signInWithOtp" not in js
    assert "'/api/register'" in js


def test_cloudbase_password_login_and_recovery_sdk_flows_exist():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "workbench.css").read_text(encoding="utf-8")

    assert 'id="show-cloudbase-reset"' in html
    assert 'id="cloudbase-reset-panel"' in html
    assert 'id="cloudbase-reset-email"' in html
    assert 'id="cloudbase-reset-code"' in html
    assert 'id="cloudbase-reset-password"' in html
    assert 'id="cloudbase-reset-confirm"' in html
    assert 'id="cloudbase-send-reset-code"' in html
    assert 'id="cloudbase-reset-password-submit"' in html
    assert "state.cloudbaseAuth.signInWithPassword({ email, password })" in js
    assert "state.cloudbaseAuth.resetPasswordForEmail(email)" in js
    assert "pending.updateUser({ nonce: code, password })" in js
    assert "function validateCloudBasePasswordFields(mode)" in js
    assert "body.auth-open" in css
    assert '.cloudbase-auth-modal[data-mode="reset"]' in css


def test_cloudbase_registration_preserves_structured_sdk_errors():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "function cloudBaseErrorMessage(error, fallbackMessage)" in js
    assert "error_description" in js
    assert "requestId" in js
    assert "cloudBaseErrorMessage(err, '验证码发送失败')" in js


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

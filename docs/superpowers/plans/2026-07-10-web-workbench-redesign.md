# Web Workbench Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the TradingAgents web UI as a unified workbench with separate surfaces for login/identity, single-stock analysis, sector screening, report reading, and admin management.

**Architecture:** Keep the existing FastAPI backend and SSE analysis flow. Split the current static page into a small HTML shell plus plain static CSS and JavaScript served by FastAPI. The frontend owns navigation, identity persistence, report rendering, and admin form orchestration; backend changes are limited to serving static assets and preserving `/admin` compatibility.

**Tech Stack:** FastAPI, static HTML, vanilla JavaScript, CSS, Server-Sent Events, existing SQLite-backed admin store.

## Global Constraints

- Do not solve the core-vs-peripheral sector relevance engine in this UI pass.
- Do not migrate to React, Vue, or a build tool in this iteration.
- Do not change the database storage model for encrypted API keys.
- Do not change the multi-agent graph orchestration.
- The root route `/` serves the workbench.
- The existing `/admin` route remains available as a compatibility entry during this iteration, but it links users back to the integrated admin workspace.
- The primary admin experience lives inside the workbench shell.
- Use a restrained neutral palette with one primary accent and semantic status colors.
- Avoid large marketing-style hero sections.
- Avoid nested cards and decorative background effects.
- Use tables for dense candidate/admin data.
- All form controls have visible labels.
- Interactive targets are at least 44px high on touch layouts.
- Navigation has active states and keyboard-reachable controls.
- Mobile layout avoids horizontal page scroll; data tables may scroll inside their own container when necessary.
- Do not introduce a frontend build pipeline.

---

## File Structure

- Modify `tradingagents/web/api.py`: mount static assets under `/assets` and continue serving `/` and `/admin`.
- Replace `tradingagents/web/static/index.html`: keep only the workbench shell markup and references to external CSS/JS.
- Replace `tradingagents/web/static/admin.html`: compatibility bridge that points to `/?view=admin`.
- Create `tradingagents/web/static/workbench.css`: visual tokens, layout, responsive behavior, tables, forms, status, report typography.
- Create `tradingagents/web/static/workbench.js`: app state, navigation, identity/admin sessions, analysis SSE, sector screening, report rendering, admin forms.
- Add or extend `tests/test_web_admin_api.py`: backend/static route coverage and admin endpoint regression coverage.
- Add `tests/test_web_workbench_static.py`: static HTML/CSS/JS structure checks that do not require a browser.

---

### Task 1: Serve Split Workbench Assets And Compatibility Admin Entry

**Files:**
- Modify: `tradingagents/web/api.py`
- Replace: `tradingagents/web/static/index.html`
- Replace: `tradingagents/web/static/admin.html`
- Create: `tradingagents/web/static/workbench.css`
- Create: `tradingagents/web/static/workbench.js`
- Test: `tests/test_web_workbench_static.py`

**Interfaces:**
- Consumes: existing `create_app()` FastAPI factory.
- Produces: `/assets/workbench.css`, `/assets/workbench.js`, `/`, and `/admin`.

- [ ] **Step 1: Write failing backend/static tests**

Create `tests/test_web_workbench_static.py` with:

```python
from fastapi.testclient import TestClient

from tradingagents.web import api


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


def test_admin_route_is_compatibility_bridge_to_workbench_admin():
    client = TestClient(api.create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'data-admin-bridge="true"' in response.text
    assert "/?view=admin" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_web_workbench_static.py -v`

Expected: FAIL because `/assets/workbench.css` and `/assets/workbench.js` are not mounted yet, and the current root page does not contain `id="app-shell"`.

- [ ] **Step 3: Mount static assets in FastAPI**

In `tradingagents/web/api.py`, import `StaticFiles` beside the response classes:

```python
from fastapi.staticfiles import StaticFiles
```

After `app = FastAPI(title="TradingAgents Web")`, add:

```python
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")
```

- [ ] **Step 4: Replace root shell HTML**

Replace `tradingagents/web/static/index.html` with a minimal workbench shell:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TradingAgents 工作台</title>
  <link rel="stylesheet" href="/assets/workbench.css">
</head>
<body>
  <div class="app-shell" id="app-shell">
    <aside class="sidebar" aria-label="主导航">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">TA</div>
        <div>
          <h1>TradingAgents</h1>
          <p>A股分析工作台</p>
        </div>
      </div>
      <nav class="nav-list" id="primary-nav">
        <button class="nav-item active" type="button" data-view="analysis">个股分析</button>
        <button class="nav-item" type="button" data-view="sector">板块筛选</button>
        <button class="nav-item" type="button" data-view="reports">报告中心</button>
        <button class="nav-item nav-admin" type="button" data-view="admin" hidden>后台管理</button>
      </nav>
      <section class="identity-card" aria-labelledby="identity-title">
        <h2 id="identity-title">访问身份</h2>
        <div class="identity-summary" id="identity-summary">未设置访问邮箱</div>
        <label for="identity-email">邮箱</label>
        <div class="inline-form">
          <input id="identity-email" type="email" autocomplete="email">
          <button class="secondary-button" type="button" id="save-identity">保存</button>
        </div>
      </section>
    </aside>

    <div class="main-shell">
      <header class="topbar">
        <div>
          <p class="eyebrow" id="view-eyebrow">工作台</p>
          <h2 id="view-title">个股分析</h2>
        </div>
        <div class="topbar-actions">
          <span class="state-pill" id="global-run-state">空闲</span>
          <button class="secondary-button" type="button" id="open-admin-login">管理员</button>
        </div>
      </header>

      <main class="workspace">
        <section class="view active" id="view-analysis" data-title="个股分析" data-eyebrow="多 Agent 分析">
          <div id="analysis-root"></div>
        </section>
        <section class="view" id="view-sector" data-title="板块筛选" data-eyebrow="候选池">
          <div id="sector-root"></div>
        </section>
        <section class="view" id="view-reports" data-title="报告中心" data-eyebrow="Markdown 报告">
          <div id="reports-root"></div>
        </section>
        <section class="view" id="view-admin" data-title="后台管理" data-eyebrow="模型与白名单">
          <div id="admin-root"></div>
        </section>
      </main>
    </div>
  </div>

  <div class="modal-backdrop" id="admin-modal" hidden>
    <section class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="admin-modal-title">
      <div class="modal-header">
        <h2 id="admin-modal-title">管理员登录</h2>
        <button class="icon-button" type="button" id="close-admin-modal" aria-label="关闭">×</button>
      </div>
      <div id="admin-auth-root"></div>
    </section>
  </div>

  <script src="/assets/workbench.js" defer></script>
</body>
</html>
```

- [ ] **Step 5: Create initial CSS and JS assets**

Create `tradingagents/web/static/workbench.css` with a minimal but valid shell style:

```css
:root {
  color-scheme: light;
  --bg: #f5f7f9;
  --surface: #ffffff;
  --surface-muted: #f9fafb;
  --line: #d8dee7;
  --line-soft: #edf1f5;
  --text: #17202a;
  --muted: #66758a;
  --accent: #147a63;
  --accent-soft: #e3f2ed;
  --active: #116d84;
  --active-soft: #e1f0f4;
  --danger: #b42318;
  --warning: #9a5b00;
  --radius: 8px;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

button,
input,
select,
textarea {
  font: inherit;
}

button {
  min-height: 38px;
  border: 0;
  border-radius: 6px;
  padding: 0 12px;
  background: var(--accent);
  color: #fff;
  font-weight: 700;
  cursor: pointer;
}

button:disabled {
  opacity: .55;
  cursor: not-allowed;
}

.secondary-button,
.icon-button {
  border: 1px solid var(--line);
  background: #fff;
  color: var(--text);
}

.app-shell {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  min-height: 100vh;
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 18px;
  border-right: 1px solid var(--line);
  background: var(--surface);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  font-weight: 800;
}

.brand h1,
.brand p,
.topbar h2,
.topbar p {
  margin: 0;
}

.brand h1 { font-size: 17px; }
.brand p,
.eyebrow { color: var(--muted); font-size: 12px; }

.nav-list {
  display: grid;
  gap: 6px;
}

.nav-item {
  justify-content: flex-start;
  width: 100%;
  min-height: 42px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--muted);
  text-align: left;
}

.nav-item.active {
  border-color: #b7dce5;
  background: var(--active-soft);
  color: var(--active);
}

.identity-card,
.panel {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
}

.identity-card {
  padding: 12px;
  margin-top: auto;
}

.identity-card h2 {
  margin: 0 0 8px;
  font-size: 13px;
}

label {
  display: block;
  margin: 10px 0 6px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}

input:not([type="checkbox"]),
select,
textarea {
  width: 100%;
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 10px;
  background: #fff;
  color: var(--text);
}

.inline-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
}

.main-shell {
  min-width: 0;
}

.topbar {
  min-height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, .94);
  position: sticky;
  top: 0;
  z-index: 4;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.state-pill {
  display: inline-flex;
  align-items: center;
  min-height: 30px;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 0 10px;
  background: #fff;
  color: var(--muted);
  white-space: nowrap;
}

.workspace {
  padding: 20px;
}

.view { display: none; }
.view.active { display: block; }

.modal-backdrop {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 18px;
  background: rgba(23, 32, 42, .42);
  z-index: 20;
}

.modal-panel {
  width: min(440px, 100%);
  border-radius: 10px;
  background: var(--surface);
  box-shadow: 0 24px 80px rgba(23, 32, 42, .22);
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
}

.modal-header h2 { margin: 0; font-size: 16px; }

@media (max-width: 860px) {
  .app-shell { grid-template-columns: 1fr; }
  .sidebar { border-right: 0; border-bottom: 1px solid var(--line); }
  .nav-list { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .nav-item { text-align: center; justify-content: center; }
  .topbar { position: static; align-items: flex-start; flex-direction: column; }
  .workspace { padding: 14px; }
}
```

Create `tradingagents/web/static/workbench.js` with:

```javascript
(() => {
  const state = {
    view: 'analysis',
    adminToken: localStorage.getItem('ta_admin_token') || '',
    identityEmail: localStorage.getItem('ta_identity_email') || '',
    runState: '空闲'
  };

  function qs(selector) {
    return document.querySelector(selector);
  }

  function setText(selector, text) {
    const node = qs(selector);
    if (node) node.textContent = text;
  }

  function showView(view) {
    state.view = view;
    document.querySelectorAll('.view').forEach(section => {
      section.classList.toggle('active', section.id === `view-${view}`);
    });
    document.querySelectorAll('.nav-item').forEach(button => {
      button.classList.toggle('active', button.dataset.view === view);
    });
    const active = qs(`#view-${view}`);
    setText('#view-title', active?.dataset.title || '工作台');
    setText('#view-eyebrow', active?.dataset.eyebrow || 'TradingAgents');
    history.replaceState(null, '', `?view=${encodeURIComponent(view)}`);
  }

  function boot() {
    const params = new URLSearchParams(location.search);
    const requestedView = params.get('view') || state.view;
    if (state.identityEmail) {
      const input = qs('#identity-email');
      if (input) input.value = state.identityEmail;
      setText('#identity-summary', state.identityEmail);
    }
    document.querySelectorAll('[data-view]').forEach(button => {
      button.addEventListener('click', () => showView(button.dataset.view));
    });
    qs('#save-identity')?.addEventListener('click', () => {
      const email = qs('#identity-email')?.value.trim() || '';
      state.identityEmail = email;
      localStorage.setItem('ta_identity_email', email);
      setText('#identity-summary', email || '未设置访问邮箱');
    });
    showView(requestedView);
  }

  window.TradingAgentsWorkbench = { state, showView };
  document.addEventListener('DOMContentLoaded', boot);
})();
```

- [ ] **Step 6: Replace `/admin` page with compatibility bridge**

Replace `tradingagents/web/static/admin.html` with:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TradingAgents 后台</title>
  <meta http-equiv="refresh" content="0; url=/?view=admin">
</head>
<body data-admin-bridge="true">
  <p>后台管理已整合到工作台。<a href="/?view=admin">打开后台管理</a></p>
</body>
</html>
```

- [ ] **Step 7: Run verification**

Run:

```bash
python3 -m py_compile tradingagents/web/api.py
node --check tradingagents/web/static/workbench.js
python3 -m pytest tests/test_web_workbench_static.py -v
```

Expected:

- `py_compile` exits 0.
- `node --check` exits 0.
- pytest passes when test dependencies are installed; if pytest is unavailable, record `No module named pytest`.

- [ ] **Step 8: Commit Task 1**

```bash
git add tradingagents/web/api.py tradingagents/web/static/index.html tradingagents/web/static/admin.html tradingagents/web/static/workbench.css tradingagents/web/static/workbench.js tests/test_web_workbench_static.py
git commit -m "refactor: add web workbench shell"
```

---

### Task 2: Add Workbench Navigation, Identity, And Admin Login State

**Files:**
- Modify: `tradingagents/web/static/index.html`
- Modify: `tradingagents/web/static/workbench.css`
- Modify: `tradingagents/web/static/workbench.js`
- Test: `tests/test_web_workbench_static.py`

**Interfaces:**
- Consumes: `TradingAgentsWorkbench.state`, `showView(view: string)`.
- Produces: `identityQuery(): URLSearchParams`, `adminHeaders(): HeadersInit`, `refreshAdminStatus(): Promise<void>`.

- [ ] **Step 1: Extend static structure tests**

Append to `tests/test_web_workbench_static.py`:

```python
from pathlib import Path


STATIC_DIR = Path("tradingagents/web/static")


def test_workbench_has_required_navigation_and_identity_targets():
    html = (STATIC_DIR / "index.html").read_text()
    js = (STATIC_DIR / "workbench.js").read_text()

    assert 'data-view="analysis"' in html
    assert 'data-view="sector"' in html
    assert 'data-view="reports"' in html
    assert 'data-view="admin"' in html
    assert 'id="identity-email"' in html
    assert 'function identityQuery()' in js
    assert 'function adminHeaders()' in js
    assert 'function refreshAdminStatus()' in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web_workbench_static.py::test_workbench_has_required_navigation_and_identity_targets -v`

Expected: FAIL because `identityQuery`, `adminHeaders`, and `refreshAdminStatus` are not implemented yet.

- [ ] **Step 3: Add identity and admin session helpers**

In `workbench.js`, inside the IIFE and before `boot()`, add:

```javascript
  function identityQuery() {
    const params = new URLSearchParams();
    if (state.identityEmail) params.set('access_email', state.identityEmail);
    return params;
  }

  function adminHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    if (state.adminToken) headers.Authorization = `Bearer ${state.adminToken}`;
    return headers;
  }

  async function apiJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  function setAdminAvailable(available) {
    document.querySelectorAll('.nav-admin').forEach(button => {
      button.hidden = !available;
    });
  }

  async function refreshAdminStatus() {
    const status = await apiJson('/api/admin/status');
    state.adminPasswordConfigured = Boolean(status.password_configured);
    setAdminAvailable(Boolean(state.adminToken));
    renderAdminAuth();
  }
```

- [ ] **Step 4: Add admin modal auth rendering**

In `workbench.js`, add:

```javascript
  function openAdminModal() {
    const modal = qs('#admin-modal');
    if (modal) modal.hidden = false;
    renderAdminAuth();
  }

  function closeAdminModal() {
    const modal = qs('#admin-modal');
    if (modal) modal.hidden = true;
  }

  function renderAdminAuth() {
    const root = qs('#admin-auth-root');
    if (!root) return;
    const isSetup = !state.adminPasswordConfigured;
    root.innerHTML = `
      <div class="modal-body">
        <p class="helper-text">${isSetup ? '首次使用请设置管理员密码。' : '输入管理员密码进入后台管理。'}</p>
        <label for="admin-password">${isSetup ? '设置管理员密码' : '管理员密码'}</label>
        <input id="admin-password" type="password" autocomplete="${isSetup ? 'new-password' : 'current-password'}">
        <div class="form-status" id="admin-auth-status"></div>
        <button type="button" id="admin-auth-submit">${isSetup ? '设置并登录' : '登录'}</button>
      </div>
    `;
    qs('#admin-auth-submit')?.addEventListener('click', submitAdminAuth);
  }

  async function submitAdminAuth() {
    const password = qs('#admin-password')?.value || '';
    const status = qs('#admin-auth-status');
    try {
      if (!state.adminPasswordConfigured) {
        await apiJson('/api/admin/setup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password })
        });
        state.adminPasswordConfigured = true;
      }
      const login = await apiJson('/api/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
      });
      state.adminToken = login.token || '';
      localStorage.setItem('ta_admin_token', state.adminToken);
      setAdminAvailable(true);
      closeAdminModal();
      showView('admin');
      renderAdminWorkspace();
    } catch (err) {
      if (status) status.textContent = `登录失败：${err.message}`;
    }
  }
```

- [ ] **Step 5: Wire modal buttons during boot**

In `boot()`, add:

```javascript
    qs('#open-admin-login')?.addEventListener('click', openAdminModal);
    qs('#close-admin-modal')?.addEventListener('click', closeAdminModal);
    refreshAdminStatus().catch(err => {
      setText('#identity-summary', `后台状态读取失败：${err.message}`);
    });
```

Export helpers at the bottom:

```javascript
  window.TradingAgentsWorkbench = {
    state,
    showView,
    identityQuery,
    adminHeaders,
    refreshAdminStatus
  };
```

- [ ] **Step 6: Add CSS for modal body and helper text**

Append to `workbench.css`:

```css
.modal-body {
  display: grid;
  gap: 10px;
  padding: 16px;
}

.helper-text,
.form-status {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.form-status {
  min-height: 20px;
  color: var(--danger);
}
```

- [ ] **Step 7: Run verification**

Run:

```bash
node --check tradingagents/web/static/workbench.js
python3 -m pytest tests/test_web_workbench_static.py -v
```

Expected: JS syntax check exits 0; static tests pass when pytest is available.

- [ ] **Step 8: Commit Task 2**

```bash
git add tradingagents/web/static/index.html tradingagents/web/static/workbench.css tradingagents/web/static/workbench.js tests/test_web_workbench_static.py
git commit -m "feat: add workbench identity and admin login"
```

---

### Task 3: Rebuild Single-Stock Analysis Workspace

**Files:**
- Modify: `tradingagents/web/static/workbench.css`
- Modify: `tradingagents/web/static/workbench.js`
- Test: `tests/test_web_workbench_static.py`

**Interfaces:**
- Consumes: `identityQuery()`, `showView(view)`, existing `/api/events` SSE endpoint.
- Produces: `setAnalysisTicker(ticker: string): void`, `renderAnalysisWorkspace(): void`.

- [ ] **Step 1: Add static test for analysis workspace functions**

Append to `tests/test_web_workbench_static.py`:

```python
def test_workbench_js_contains_analysis_workspace_contract():
    js = (STATIC_DIR / "workbench.js").read_text()

    assert "function renderAnalysisWorkspace()" in js
    assert "function setAnalysisTicker(ticker)" in js
    assert "new EventSource" in js
    assert "run_started" in js
    assert "report_section_updated" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web_workbench_static.py::test_workbench_js_contains_analysis_workspace_contract -v`

Expected: FAIL because analysis workspace functions are not implemented.

- [ ] **Step 3: Add analysis constants and state**

In `workbench.js`, add near the top:

```javascript
  const COLLAPSE_LIMIT = 260;
  const TEAM_ROLES = {
    market: { label: '市场分析师', kind: '价格/技术面', report: 'market_report' },
    news: { label: '新闻分析师', kind: '资讯催化', report: 'news_report' },
    fundamentals: { label: '基本面分析师', kind: '财务/估值', report: 'fundamentals_report' },
    social: { label: '情绪分析师', kind: '社交/舆情', report: 'sentiment_report' },
    research_manager: { label: '研究经理', kind: '多空观点整合', report: 'investment_plan' },
    trader: { label: '交易员', kind: '交易计划', report: 'trader_investment_plan' },
    portfolio_manager: { label: '组合经理', kind: '最终决策', report: 'final_trade_decision' }
  };
  const SECTION_TO_ROLE = Object.fromEntries(
    Object.entries(TEAM_ROLES).map(([key, role]) => [role.report, key])
  );

  Object.assign(state, {
    source: null,
    streamDone: false,
    eventTotal: 0,
    roleStates: {},
    reports: {}
  });
```

- [ ] **Step 4: Render the analysis workspace**

Add:

```javascript
  function renderAnalysisWorkspace() {
    const root = qs('#analysis-root');
    if (!root) return;
    root.innerHTML = `
      <div class="workspace-grid analysis-grid">
        <section class="panel form-panel">
          <div class="panel-header">
            <h3>分析参数</h3>
            <p>选择股票和分析团队</p>
          </div>
          <div class="panel-body">
            <label for="ticker">股票代码</label>
            <input id="ticker" value="600519.SH" autocomplete="off">
            <label for="trade-date">分析日期</label>
            <input id="trade-date" type="date">
            <fieldset class="module-fieldset">
              <legend>分析模块</legend>
              <label class="check-option"><input type="checkbox" name="analyst" value="market" checked>市场</label>
              <label class="check-option"><input type="checkbox" name="analyst" value="news" checked>新闻</label>
              <label class="check-option"><input type="checkbox" name="analyst" value="fundamentals" checked>基本面</label>
              <label class="check-option"><input type="checkbox" name="analyst" value="social">社交情绪</label>
            </fieldset>
            <button type="button" id="run-analysis">开始分析</button>
            <div class="status-box" id="analysis-status">等待开始</div>
          </div>
        </section>
        <section class="panel run-panel">
          <div class="panel-header">
            <h3>Agent 团队</h3>
            <p id="current-agent">未开始</p>
          </div>
          <div class="team-board" id="team-board"></div>
        </section>
        <section class="panel timeline-panel">
          <div class="panel-header">
            <h3>过程</h3>
            <p><span id="event-count">0</span> 个事件</p>
          </div>
          <div class="timeline" id="log"><div class="empty-state">等待过程事件</div></div>
        </section>
        <section class="panel report-preview-panel">
          <div class="panel-header">
            <h3>报告预览</h3>
            <button class="secondary-button compact-button" type="button" data-view="reports">打开报告中心</button>
          </div>
          <div class="report-preview" id="report-preview"><div class="empty-state">等待报告生成</div></div>
        </section>
      </div>
    `;
    const dateInput = qs('#trade-date');
    if (dateInput && !dateInput.value) dateInput.value = new Date().toISOString().slice(0, 10);
    qs('#run-analysis')?.addEventListener('click', startAnalysis);
    qs('[data-view="reports"]')?.addEventListener('click', () => showView('reports'));
    resetTeamBoard(selectedAnalystList());
  }
```

- [ ] **Step 5: Add analysis behavior**

Add the adapted functions from the old page:

```javascript
  function selectedAnalystList() {
    const selected = [...document.querySelectorAll('input[name="analyst"]:checked')].map(input => input.value);
    return selected.length ? selected : ['market', 'news', 'fundamentals'];
  }

  function getSelectedAnalysts() {
    return selectedAnalystList().join(',');
  }

  function setAnalysisTicker(ticker) {
    const input = qs('#ticker');
    if (input) input.value = ticker;
  }

  function resetTeamBoard(analysts) {
    state.roleStates = {};
    [...analysts, 'research_manager', 'trader', 'portfolio_manager'].forEach(key => {
      state.roleStates[key] = 'pending';
    });
    renderTeamBoard();
  }

  function renderTeamBoard() {
    const board = qs('#team-board');
    if (!board) return;
    board.textContent = '';
    Object.entries(state.roleStates).forEach(([key, roleState]) => {
      const role = TEAM_ROLES[key];
      if (!role) return;
      const card = document.createElement('div');
      card.className = `role-card ${roleState}`;
      card.innerHTML = `<div><strong></strong><span></span></div><p></p>`;
      card.querySelector('strong').textContent = role.label;
      card.querySelector('span').textContent = roleState === 'active' ? '进行中' : roleState === 'done' ? '已完成' : '待处理';
      card.querySelector('p').textContent = role.kind;
      board.appendChild(card);
    });
  }

  function markRole(key, nextState) {
    if (!state.roleStates[key]) return;
    if (nextState === 'active') {
      Object.keys(state.roleStates).forEach(name => {
        if (state.roleStates[name] === 'active') state.roleStates[name] = 'done';
      });
    }
    state.roleStates[key] = nextState;
    setText('#current-agent', TEAM_ROLES[key]?.label || '团队协作');
    renderTeamBoard();
  }

  function setRunState(text, mode) {
    state.runState = text;
    const pill = qs('#global-run-state');
    if (pill) {
      pill.textContent = text;
      pill.className = `state-pill ${mode || ''}`.trim();
    }
  }

  function tickEvent() {
    state.eventTotal += 1;
    setText('#event-count', String(state.eventTotal));
  }

  function addCollapsibleLog(title, detail, meta = '', kind = '') {
    tickEvent();
    const log = qs('#log');
    if (!log) return;
    log.querySelector('.empty-state')?.remove();
    const item = document.createElement('article');
    item.className = `event-item ${kind}`.trim();
    const safeDetail = String(detail || '');
    item.innerHTML = `<div class="event-title"><strong></strong><span></span></div><p></p>`;
    item.querySelector('strong').textContent = title;
    item.querySelector('span').textContent = meta;
    item.querySelector('p').textContent = safeDetail.length > COLLAPSE_LIMIT ? `${safeDetail.slice(0, COLLAPSE_LIMIT)}...` : safeDetail;
    if (safeDetail.length > COLLAPSE_LIMIT) {
      const details = document.createElement('details');
      details.innerHTML = `<summary>展开完整输出（${safeDetail.length} 字）</summary><pre></pre>`;
      details.querySelector('pre').textContent = safeDetail;
      item.appendChild(details);
    }
    log.prepend(item);
  }

  function resetRunView() {
    if (state.source) state.source.close();
    state.reports = {};
    state.streamDone = false;
    state.eventTotal = 0;
    setText('#event-count', '0');
    setText('#current-agent', '连接中');
    const log = qs('#log');
    if (log) log.innerHTML = '<div class="empty-state">等待过程事件</div>';
    const preview = qs('#report-preview');
    if (preview) preview.innerHTML = '<div class="empty-state">等待报告生成</div>';
    resetTeamBoard(selectedAnalystList());
  }

  function startAnalysis() {
    resetRunView();
    const button = qs('#run-analysis');
    if (button) button.disabled = true;
    setRunState('连接中', 'running');
    setText('#analysis-status', '连接中');
    const params = new URLSearchParams({
      ticker: qs('#ticker')?.value.trim() || '',
      trade_date: qs('#trade-date')?.value || new Date().toISOString().slice(0, 10),
      analysts: getSelectedAnalysts()
    });
    identityQuery().forEach((value, key) => params.set(key, value));
    state.source = new EventSource(`/api/events?${params}`);
    ['run_started', 'tool_called', 'agent_message', 'report_section_updated', 'run_completed', 'run_failed'].forEach(name => {
      state.source.addEventListener(name, event => handleAnalysisEvent(name, JSON.parse(event.data)));
    });
    state.source.onerror = () => {
      if (state.streamDone) return;
      setRunState('连接中断', 'failed');
      setText('#analysis-status', '连接中断或服务端报错');
      if (button) button.disabled = false;
      if (state.source) state.source.close();
    };
  }
```

- [ ] **Step 6: Add event handler**

Add:

```javascript
  function handleAnalysisEvent(event, data) {
    if (event === 'run_started') {
      resetTeamBoard(data.analysts || []);
      setRunState('分析中', 'running');
      setText('#current-agent', '团队启动');
      setText('#analysis-status', `分析中：${data.ticker} / ${data.trade_date}`);
      addCollapsibleLog('任务启动', `${data.ticker} ${(data.analysts || []).join('、')}`, '团队', 'active');
    } else if (event === 'tool_called') {
      setRunState('分析中', 'running');
      addCollapsibleLog('工具调用', `${data.tool} ${JSON.stringify(data.args)}`, '工具', 'active');
    } else if (event === 'agent_message') {
      setRunState('分析中', 'running');
      addCollapsibleLog('Agent 输出', data.content, data.message_type || 'Agent', 'active');
    } else if (event === 'report_section_updated') {
      const roleKey = SECTION_TO_ROLE[data.section];
      if (roleKey) markRole(roleKey, 'done');
      state.reports[data.section] = data.content || '';
      renderReportPreview();
      renderReportCenter();
      addCollapsibleLog('报告更新', `${TEAM_ROLES[roleKey]?.label || data.section} 交付了 ${data.section}`, '协作轨迹', 'done');
    } else if (event === 'run_completed') {
      state.streamDone = true;
      Object.keys(state.roleStates).forEach(key => { state.roleStates[key] = 'done'; });
      renderTeamBoard();
      setRunState('分析完成', 'done');
      setText('#current-agent', '已结束');
      setText('#analysis-status', '分析完成');
      addCollapsibleLog('完成', '最终状态已生成，团队分析已结束。', '系统', 'done');
      const button = qs('#run-analysis');
      if (button) button.disabled = false;
      if (state.source) state.source.close();
    } else if (event === 'run_failed') {
      state.streamDone = true;
      const detail = `${data.error_type || 'Error'}: ${data.message || '未知错误'}`;
      setRunState('分析失败', 'failed');
      setText('#current-agent', '已停止');
      setText('#analysis-status', `分析失败：${detail}`);
      addCollapsibleLog('错误', detail, '系统', 'error');
      const button = qs('#run-analysis');
      if (button) button.disabled = false;
      if (state.source) state.source.close();
    }
  }
```

- [ ] **Step 7: Add CSS for analysis workspace**

Append layout classes for `.workspace-grid`, `.analysis-grid`, `.panel-header`, `.panel-body`, `.team-board`, `.role-card`, `.timeline`, `.event-item`, `.status-box`, `.empty-state`, `.module-fieldset`, and `.check-option`. Use 8px radius or less and avoid nested card styling.

- [ ] **Step 8: Wire rendering during boot and export function**

In `boot()`, call:

```javascript
    renderAnalysisWorkspace();
```

Update export:

```javascript
    setAnalysisTicker,
    renderAnalysisWorkspace
```

- [ ] **Step 9: Run verification**

Run:

```bash
node --check tradingagents/web/static/workbench.js
python3 -m pytest tests/test_web_workbench_static.py -v
```

Expected: JS syntax check exits 0; static tests pass when pytest is available.

- [ ] **Step 10: Commit Task 3**

```bash
git add tradingagents/web/static/workbench.css tradingagents/web/static/workbench.js tests/test_web_workbench_static.py
git commit -m "feat: rebuild single stock analysis workspace"
```

---

### Task 4: Rebuild Sector Screening Workspace

**Files:**
- Modify: `tradingagents/web/static/workbench.css`
- Modify: `tradingagents/web/static/workbench.js`
- Test: `tests/test_web_workbench_static.py`

**Interfaces:**
- Consumes: `identityQuery()`, `setAnalysisTicker(ticker)`, `showView(view)`, existing `/api/sector/screen`.
- Produces: `renderSectorWorkspace(): void`, `renderSectorResults(data: object): void`.

- [ ] **Step 1: Add static test for sector workspace contract**

Append:

```python
def test_workbench_js_contains_sector_workspace_contract():
    js = (STATIC_DIR / "workbench.js").read_text()

    assert "function renderSectorWorkspace()" in js
    assert "function renderSectorResults(data)" in js
    assert "/api/sector/screen" in js
    assert "setAnalysisTicker(item.ticker)" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web_workbench_static.py::test_workbench_js_contains_sector_workspace_contract -v`

Expected: FAIL because sector workspace functions are not implemented.

- [ ] **Step 3: Add sector workspace rendering**

Add:

```javascript
  function renderSectorWorkspace() {
    const root = qs('#sector-root');
    if (!root) return;
    root.innerHTML = `
      <div class="workspace-grid sector-grid">
        <section class="panel form-panel">
          <div class="panel-header">
            <h3>筛选条件</h3>
            <p>生成候选池，不自动分析</p>
          </div>
          <div class="panel-body">
            <label for="sector-name">板块名称</label>
            <input id="sector-name" value="白酒" autocomplete="off">
            <label for="sector-board-type">板块类型</label>
            <select id="sector-board-type">
              <option value="auto">自动识别</option>
              <option value="concept">概念板块</option>
              <option value="industry">行业板块</option>
            </select>
            <label for="sector-top-n">候选数量</label>
            <input id="sector-top-n" type="number" min="1" max="50" value="10">
            <label class="check-option single-check"><input type="checkbox" id="sector-ai-review">AI 复核候选池</label>
            <button type="button" id="screen-sector">筛选板块</button>
            <div class="status-box" id="sector-status">等待筛选</div>
          </div>
        </section>
        <section class="panel sector-results-panel">
          <div class="panel-header">
            <h3>板块候选池</h3>
            <p id="sector-summary">暂无候选</p>
          </div>
          <div class="table-wrap" id="sector-results"><div class="empty-state">输入板块后生成候选股</div></div>
        </section>
      </div>
    `;
    qs('#screen-sector')?.addEventListener('click', screenSector);
  }
```

- [ ] **Step 4: Add sector fetch and rendering**

Add:

```javascript
  async function screenSector() {
    const sector = qs('#sector-name')?.value.trim() || '';
    if (!sector) {
      setText('#sector-status', '请输入板块名称');
      return;
    }
    const button = qs('#screen-sector');
    if (button) button.disabled = true;
    setText('#sector-status', '筛选中');
    const results = qs('#sector-results');
    if (results) results.innerHTML = '<div class="empty-state">正在拉取板块成分并计算数据综合分</div>';
    const params = new URLSearchParams({
      sector,
      top_n: qs('#sector-top-n')?.value || '10',
      board_type: qs('#sector-board-type')?.value || 'auto',
      ai_review: qs('#sector-ai-review')?.checked ? 'true' : 'false'
    });
    identityQuery().forEach((value, key) => params.set(key, value));
    try {
      const data = await apiJson(`/api/sector/screen?${params}`);
      renderSectorResults(data);
      setText('#sector-status', `已生成 ${data.candidates.length} 只候选股`);
    } catch (err) {
      setText('#sector-status', `筛选失败：${err.message}`);
      if (results) results.innerHTML = '<div class="empty-state">板块筛选失败，请检查身份、板块名称或数据源</div>';
    } finally {
      if (button) button.disabled = false;
    }
  }

  function renderSectorResults(data) {
    const root = qs('#sector-results');
    if (!root) return;
    setText('#sector-summary', `${data.sector} / ${data.board_type} / ${data.candidates.length} 只候选`);
    root.textContent = '';
    const table = document.createElement('table');
    table.className = 'data-table sector-table';
    table.innerHTML = `<thead><tr>
      <th>排名</th><th>股票</th><th>综合分</th><th>分项</th><th>理由 / 风险</th><th>AI 复核</th><th>操作</th>
    </tr></thead><tbody></tbody>`;
    const body = table.querySelector('tbody');
    (data.candidates || []).forEach((item, index) => {
      const scores = item.component_scores || {};
      const aiReview = item.ai_review || null;
      const tr = document.createElement('tr');
      tr.innerHTML = `<td></td><td></td><td></td><td></td><td></td><td></td><td></td>`;
      tr.children[0].textContent = String(index + 1);
      tr.children[1].innerHTML = `<strong></strong><div class="muted"></div>`;
      tr.children[1].querySelector('strong').textContent = item.name || item.ticker;
      tr.children[1].querySelector('.muted').textContent = item.ticker;
      const scoreValue = item.score === undefined || item.score === null ? '' : item.score;
      const relevance = scores.relevance === undefined || scores.relevance === null ? '-' : scores.relevance;
      const trend = scores.trend === undefined || scores.trend === null ? '-' : scores.trend;
      const valuation = scores.valuation === undefined || scores.valuation === null ? '-' : scores.valuation;
      const liquidity = scores.liquidity === undefined || scores.liquidity === null ? '-' : scores.liquidity;
      tr.children[2].innerHTML = `<span class="score-badge">${scoreValue}</span>`;
      tr.children[3].textContent = `相关 ${relevance} / 趋势 ${trend} / 估值 ${valuation} / 流动性 ${liquidity}`;
      const risks = item.risks && item.risks.length ? item.risks.join('；') : '暂无显著规则风险';
      tr.children[4].textContent = `${(item.reasons || []).join('；')}｜${risks}`;
      tr.children[5].textContent = aiReview
        ? `${aiReview.action || '观察'} / ${aiReview.confidence || '中'}｜${aiReview.reason || ''}`
        : '未复核';
      const action = document.createElement('button');
      action.type = 'button';
      action.className = 'secondary-button compact-button';
      action.textContent = '带入分析';
      action.addEventListener('click', () => {
        showView('analysis');
        setAnalysisTicker(item.ticker);
        setText('#analysis-status', `已带入 ${item.ticker}，确认模块后手动开始分析`);
      });
      tr.children[6].appendChild(action);
      body.appendChild(tr);
    });
    root.appendChild(table);
    if (data.ai_review) {
      const note = document.createElement('div');
      note.className = `notice ${data.ai_review.status === 'completed' ? 'success' : 'warning'}`;
      note.textContent = data.ai_review.status === 'completed'
        ? `AI 复核完成：${data.ai_review.summary || '已为候选池补充定性复核。'}`
        : `AI 复核不可用：${data.ai_review.error || '未返回复核结果。'}`;
      root.appendChild(note);
    }
    const report = document.createElement('section');
    report.className = 'markdown sector-report';
    report.innerHTML = renderMarkdown(data.report || '');
    root.appendChild(report);
  }
```

- [ ] **Step 5: Add CSS for sector table**

Append classes for `.sector-grid`, `.table-wrap`, `.data-table`, `.score-badge`, `.notice`, `.muted`, and `.sector-report`. Ensure table scroll is inside `.table-wrap` on mobile.

- [ ] **Step 6: Wire rendering during boot and export functions**

In `boot()`, call:

```javascript
    renderSectorWorkspace();
```

Update export:

```javascript
    renderSectorWorkspace,
    renderSectorResults
```

- [ ] **Step 7: Run verification**

Run:

```bash
node --check tradingagents/web/static/workbench.js
python3 -m pytest tests/test_web_workbench_static.py -v
```

Expected: JS syntax check exits 0; static tests pass when pytest is available.

- [ ] **Step 8: Commit Task 4**

```bash
git add tradingagents/web/static/workbench.css tradingagents/web/static/workbench.js tests/test_web_workbench_static.py
git commit -m "feat: rebuild sector screening workspace"
```

---

### Task 5: Build Report Center With Stable Tabs And Markdown Rendering

**Files:**
- Modify: `tradingagents/web/static/workbench.css`
- Modify: `tradingagents/web/static/workbench.js`
- Test: `tests/test_web_workbench_static.py`

**Interfaces:**
- Consumes: `state.reports`, `TEAM_ROLES`, `SECTION_TO_ROLE`.
- Produces: `renderMarkdown(markdown: string): string`, `renderReportCenter(): void`, `renderReportPreview(): void`.

- [ ] **Step 1: Add static test for report center contract**

Append:

```python
def test_workbench_js_contains_report_center_contract():
    js = (STATIC_DIR / "workbench.js").read_text()

    assert "function renderMarkdown(markdown)" in js
    assert "function renderReportCenter()" in js
    assert "function renderReportPreview()" in js
    assert "final_trade_decision" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web_workbench_static.py::test_workbench_js_contains_report_center_contract -v`

Expected: FAIL because report center functions are not implemented.

- [ ] **Step 3: Add Markdown renderer**

Move the existing Markdown rendering functions from the old `index.html` into `workbench.js`:

```javascript
  function escapeHtml(value) {
    return String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function renderInline(value) {
    let text = escapeHtml(value);
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    return text;
  }

  function isTableSeparator(line) {
    return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  }

  function splitTableRow(line) {
    return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
  }

  function renderMarkdown(markdown) {
    const lines = String(markdown || '').replace(/\r\n/g, '\n').split('\n');
    const output = [];
    let i = 0;
    while (i < lines.length) {
      const trimmed = lines[i].trim();
      if (!trimmed) {
        i += 1;
        continue;
      }
      if (trimmed.startsWith('```')) {
        const code = [];
        i += 1;
        while (i < lines.length && !lines[i].trim().startsWith('```')) {
          code.push(lines[i]);
          i += 1;
        }
        if (i < lines.length) i += 1;
        output.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`);
        continue;
      }
      const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
      if (heading) {
        const level = heading[1].length;
        output.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
        i += 1;
        continue;
      }
      if (trimmed.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
        const headers = splitTableRow(trimmed);
        const rows = [];
        i += 2;
        while (i < lines.length && lines[i].trim().includes('|')) {
          rows.push(splitTableRow(lines[i]));
          i += 1;
        }
        output.push('<table>');
        output.push(`<thead><tr>${headers.map(cell => `<th>${renderInline(cell)}</th>`).join('')}</tr></thead>`);
        output.push('<tbody>');
        rows.forEach(row => {
          output.push(`<tr>${row.map(cell => `<td>${renderInline(cell)}</td>`).join('')}</tr>`);
        });
        output.push('</tbody></table>');
        continue;
      }
      if (/^[-*]\s+/.test(trimmed)) {
        const items = [];
        while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
          items.push(lines[i].trim().replace(/^[-*]\s+/, ''));
          i += 1;
        }
        output.push(`<ul>${items.map(item => `<li>${renderInline(item)}</li>`).join('')}</ul>`);
        continue;
      }
      if (/^\d+\.\s+/.test(trimmed)) {
        const items = [];
        while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
          items.push(lines[i].trim().replace(/^\d+\.\s+/, ''));
          i += 1;
        }
        output.push(`<ol>${items.map(item => `<li>${renderInline(item)}</li>`).join('')}</ol>`);
        continue;
      }
      const paragraph = [trimmed];
      i += 1;
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^(#{1,4})\s+/.test(lines[i].trim()) &&
        !/^[-*]\s+/.test(lines[i].trim()) &&
        !/^\d+\.\s+/.test(lines[i].trim()) &&
        !lines[i].trim().startsWith('```') &&
        !(lines[i].trim().includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1]))
      ) {
        paragraph.push(lines[i].trim());
        i += 1;
      }
      output.push(`<p>${renderInline(paragraph.join(' '))}</p>`);
    }
    return output.join('');
  }
```

- [ ] **Step 4: Add report center rendering**

Add:

```javascript
  function reportTitle(section) {
    const roleKey = SECTION_TO_ROLE[section];
    if (roleKey && TEAM_ROLES[roleKey]) return TEAM_ROLES[roleKey].label;
    return section.replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase());
  }

  function orderedReportSections() {
    return [
      'market_report',
      'news_report',
      'fundamentals_report',
      'sentiment_report',
      'investment_plan',
      'trader_investment_plan',
      'final_trade_decision'
    ];
  }

  function renderReportPreview() {
    const root = qs('#report-preview');
    if (!root) return;
    const sections = Object.keys(state.reports);
    if (!sections.length) {
      root.innerHTML = '<div class="empty-state">等待报告生成</div>';
      return;
    }
    root.innerHTML = sections.slice(-3).map(section => `
      <button class="report-link" type="button" data-report-jump="${section}">
        <strong>${reportTitle(section)}</strong>
        <span>已生成，进入报告中心查看</span>
      </button>
    `).join('');
    root.querySelectorAll('[data-report-jump]').forEach(button => {
      button.addEventListener('click', () => {
        showView('reports');
        activateReportTab(button.dataset.reportJump);
      });
    });
  }

  function renderReportCenter() {
    const root = qs('#reports-root');
    if (!root) return;
    const sections = orderedReportSections();
    root.innerHTML = `
      <section class="panel report-center-panel">
        <div class="panel-header">
          <h3>报告中心</h3>
          <p>按团队角色分段阅读</p>
        </div>
        <div class="report-tabs" id="report-tabs" role="tablist"></div>
        <div class="report-panels" id="report-panels"></div>
      </section>
    `;
    const tabs = qs('#report-tabs');
    const panels = qs('#report-panels');
    sections.forEach((section, index) => {
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = `report-tab ${index === 0 ? 'active' : ''}`;
      tab.dataset.section = section;
      tab.role = 'tab';
      tab.textContent = reportTitle(section);
      tab.addEventListener('click', () => activateReportTab(section));
      tabs.appendChild(tab);

      const article = document.createElement('article');
      article.className = 'report-article markdown';
      article.dataset.section = section;
      article.hidden = index !== 0;
      article.innerHTML = state.reports[section]
        ? renderMarkdown(state.reports[section])
        : `<div class="empty-state">${reportTitle(section)} 尚未生成</div>`;
      panels.appendChild(article);
    });
  }

  function activateReportTab(section) {
    document.querySelectorAll('.report-tab').forEach(tab => {
      const active = tab.dataset.section === section;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
      tab.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll('.report-article').forEach(panel => {
      panel.hidden = panel.dataset.section !== section;
    });
  }
```

- [ ] **Step 5: Add report CSS**

Append styles for `.report-center-panel`, `.report-tabs`, `.report-tab`, `.report-article`, `.markdown`, `.report-link`. Use readable line length and table styling.

- [ ] **Step 6: Wire rendering during boot and export**

In `boot()`, call:

```javascript
    renderReportCenter();
```

Update export:

```javascript
    renderMarkdown,
    renderReportCenter,
    renderReportPreview
```

- [ ] **Step 7: Run verification**

Run:

```bash
node --check tradingagents/web/static/workbench.js
python3 -m pytest tests/test_web_workbench_static.py -v
```

Expected: JS syntax check exits 0; static tests pass when pytest is available.

- [ ] **Step 8: Commit Task 5**

```bash
git add tradingagents/web/static/workbench.css tradingagents/web/static/workbench.js tests/test_web_workbench_static.py
git commit -m "feat: add workbench report center"
```

---

### Task 6: Integrate Admin Management Into The Workbench

**Files:**
- Modify: `tradingagents/web/static/workbench.css`
- Modify: `tradingagents/web/static/workbench.js`
- Test: `tests/test_web_workbench_static.py`
- Test: `tests/test_web_admin_api.py`

**Interfaces:**
- Consumes: `adminHeaders()`, existing `/api/admin/*` endpoints.
- Produces: `renderAdminWorkspace(): void`, `loadAdminData(): Promise<void>`.

- [ ] **Step 1: Add static test for admin workspace contract**

Append to `tests/test_web_workbench_static.py`:

```python
def test_workbench_js_contains_admin_workspace_contract():
    js = (STATIC_DIR / "workbench.js").read_text()

    assert "function renderAdminWorkspace()" in js
    assert "function loadAdminData()" in js
    assert "/api/admin/whitelist" in js
    assert "/api/admin/model-configs" in js
    assert "api_key" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web_workbench_static.py::test_workbench_js_contains_admin_workspace_contract -v`

Expected: FAIL because admin workspace functions are not implemented.

- [ ] **Step 3: Add admin workspace rendering**

Add:

```javascript
  function renderAdminWorkspace() {
    const root = qs('#admin-root');
    if (!root) return;
    if (!state.adminToken) {
      root.innerHTML = `
        <section class="panel">
          <div class="panel-header">
            <h3>后台管理</h3>
            <p>需要管理员登录</p>
          </div>
          <div class="panel-body">
            <p class="helper-text">登录后可配置白名单和模型 API Key。</p>
            <button type="button" id="admin-login-from-view">管理员登录</button>
          </div>
        </section>
      `;
      qs('#admin-login-from-view')?.addEventListener('click', openAdminModal);
      return;
    }
    root.innerHTML = `
      <div class="workspace-grid admin-grid">
        <section class="panel">
          <div class="panel-header">
            <h3>模型配置</h3>
            <p>API Key 加密入库，保存后只显示掩码</p>
          </div>
          <div class="panel-body admin-form">
            <label for="model-name">显示名称</label>
            <input id="model-name" value="DeepSeek 默认">
            <label for="model-provider">供应商</label>
            <select id="model-provider">
              <option value="deepseek">DeepSeek</option>
              <option value="openai">OpenAI</option>
              <option value="openai_compatible">OpenAI-compatible</option>
              <option value="anthropic">Anthropic</option>
              <option value="google">Google</option>
              <option value="qwen-cn">通义千问</option>
            </select>
            <label for="model-base-url">Base URL</label>
            <input id="model-base-url">
            <p class="helper-text">留空使用供应商默认地址。</p>
            <label for="model-quick">快速模型</label>
            <input id="model-quick" value="deepseek-chat">
            <label for="model-deep">深度模型</label>
            <input id="model-deep" value="deepseek-reasoner">
            <label for="model-api-key">API Key</label>
            <input id="model-api-key" type="password" autocomplete="off">
            <button type="button" id="save-model-config">保存模型</button>
            <div class="status-box" id="model-status">等待保存</div>
          </div>
        </section>
        <section class="panel">
          <div class="panel-header">
            <h3>白名单</h3>
            <p>控制可访问用户</p>
          </div>
          <div class="panel-body admin-form">
            <label for="wl-email">邮箱</label>
            <input id="wl-email" autocomplete="off">
            <label for="wl-uid">UID</label>
            <input id="wl-uid" autocomplete="off">
            <label for="wl-status">状态</label>
            <select id="wl-status">
              <option value="active">启用</option>
              <option value="pending">待确认</option>
              <option value="blocked">禁用</option>
            </select>
            <label for="wl-limit">每日次数</label>
            <input id="wl-limit" type="number" min="0" value="5">
            <label for="wl-note">备注</label>
            <textarea id="wl-note"></textarea>
            <button type="button" id="save-whitelist">保存白名单</button>
            <div class="status-box" id="whitelist-status">等待保存</div>
          </div>
        </section>
        <section class="panel admin-table-panel">
          <div class="panel-header">
            <h3>当前配置</h3>
            <button class="secondary-button compact-button" type="button" id="reload-admin-data">刷新</button>
          </div>
          <div class="table-wrap" id="admin-data">等待加载</div>
        </section>
      </div>
    `;
    qs('#save-model-config')?.addEventListener('click', saveModelConfig);
    qs('#save-whitelist')?.addEventListener('click', saveWhitelist);
    qs('#reload-admin-data')?.addEventListener('click', loadAdminData);
    loadAdminData().catch(err => {
      const rootData = qs('#admin-data');
      if (rootData) rootData.textContent = `加载失败：${err.message}`;
    });
  }
```

- [ ] **Step 4: Add admin API actions**

Add:

```javascript
  async function saveModelConfig() {
    try {
      await apiJson('/api/admin/model-configs', {
        method: 'POST',
        headers: adminHeaders(),
        body: JSON.stringify({
          display_name: qs('#model-name')?.value || '',
          provider: qs('#model-provider')?.value || 'deepseek',
          base_url: qs('#model-base-url')?.value || '',
          quick_model: qs('#model-quick')?.value || '',
          deep_model: qs('#model-deep')?.value || '',
          api_key: qs('#model-api-key')?.value || ''
        })
      });
      setText('#model-status', '模型配置已保存');
      await loadAdminData();
    } catch (err) {
      setText('#model-status', `保存失败：${err.message}`);
    }
  }

  async function saveWhitelist() {
    try {
      await apiJson('/api/admin/whitelist', {
        method: 'POST',
        headers: adminHeaders(),
        body: JSON.stringify({
          email: qs('#wl-email')?.value || '',
          uid: qs('#wl-uid')?.value || '',
          status: qs('#wl-status')?.value || 'active',
          daily_limit: Number(qs('#wl-limit')?.value || 5),
          note: qs('#wl-note')?.value || ''
        })
      });
      setText('#whitelist-status', '白名单已保存');
      await loadAdminData();
    } catch (err) {
      setText('#whitelist-status', `保存失败：${err.message}`);
    }
  }

  async function loadAdminData() {
    if (!state.adminToken) return;
    const [models, whitelist] = await Promise.all([
      apiJson('/api/admin/model-configs', { headers: adminHeaders() }),
      apiJson('/api/admin/whitelist', { headers: adminHeaders() })
    ]);
    const root = qs('#admin-data');
    if (!root) return;
    root.innerHTML = `
      <h4>模型</h4>
      ${renderAdminModels(models.items || [])}
      <h4>白名单</h4>
      ${renderWhitelist(whitelist.items || [])}
    `;
  }

  function renderAdminModels(items) {
    if (!items.length) return '<div class="empty-state">暂无模型配置</div>';
    return `<table class="data-table"><thead><tr><th>名称</th><th>供应商</th><th>快速模型</th><th>深度模型</th><th>Key</th><th>状态</th></tr></thead><tbody>${
      items.map(item => `<tr><td>${escapeHtml(item.display_name)}</td><td>${escapeHtml(item.provider)}</td><td>${escapeHtml(item.quick_model)}</td><td>${escapeHtml(item.deep_model)}</td><td>${escapeHtml(item.api_key_masked)}</td><td>${item.is_default ? '默认' : '可用'}</td></tr>`).join('')
    }</tbody></table>`;
  }

  function renderWhitelist(items) {
    if (!items.length) return '<div class="empty-state">暂无白名单用户</div>';
    return `<table class="data-table"><thead><tr><th>邮箱</th><th>UID</th><th>状态</th><th>每日次数</th><th>备注</th></tr></thead><tbody>${
      items.map(item => `<tr><td>${escapeHtml(item.email)}</td><td>${escapeHtml(item.uid || '')}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.daily_limit)}</td><td>${escapeHtml(item.note || '')}</td></tr>`).join('')
    }</tbody></table>`;
  }
```

- [ ] **Step 5: Re-render admin workspace after login and navigation**

In `showView(view)`, after title updates, add:

```javascript
    if (view === 'admin') renderAdminWorkspace();
```

In `boot()`, call:

```javascript
    renderAdminWorkspace();
```

- [ ] **Step 6: Add admin CSS**

Append styles for `.admin-grid`, `.admin-form`, `.admin-table-panel`, `.compact-button`, and `.status-box` if not already present.

- [ ] **Step 7: Run verification**

Run:

```bash
node --check tradingagents/web/static/workbench.js
python3 -m pytest tests/test_web_workbench_static.py tests/test_web_admin_api.py -v
```

Expected: JS syntax check exits 0; static and admin API tests pass when pytest is available.

- [ ] **Step 8: Commit Task 6**

```bash
git add tradingagents/web/static/workbench.css tradingagents/web/static/workbench.js tests/test_web_workbench_static.py tests/test_web_admin_api.py
git commit -m "feat: integrate admin management into workbench"
```

---

### Task 7: Responsive Polish, Accessibility Pass, And End-To-End Smoke

**Files:**
- Modify: `tradingagents/web/static/workbench.css`
- Modify: `tradingagents/web/static/workbench.js`
- Test: `tests/test_web_workbench_static.py`

**Interfaces:**
- Consumes: all prior workbench functions.
- Produces: polished desktop/mobile workbench and smoke-test checklist evidence.

- [ ] **Step 1: Add static quality test**

Append:

```python
def test_workbench_static_files_follow_accessibility_basics():
    html = (STATIC_DIR / "index.html").read_text()
    css = (STATIC_DIR / "workbench.css").read_text()

    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert 'aria-label="主导航"' in html
    assert 'aria-modal="true"' in html
    assert '@media (max-width: 860px)' in css
    assert ':focus-visible' in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web_workbench_static.py::test_workbench_static_files_follow_accessibility_basics -v`

Expected: FAIL if `:focus-visible` styles have not been added.

- [ ] **Step 3: Add focus and responsive polish**

Append to `workbench.css`:

```css
:focus-visible {
  outline: 3px solid rgba(17, 109, 132, .35);
  outline-offset: 2px;
}

.data-table th,
.data-table td {
  vertical-align: top;
}

@media (max-width: 640px) {
  .nav-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .inline-form,
  .workspace-grid,
  .analysis-grid,
  .sector-grid,
  .admin-grid {
    grid-template-columns: 1fr;
  }

  .topbar-actions {
    width: 100%;
    justify-content: space-between;
  }

  .table-wrap {
    overflow-x: auto;
  }
}
```

- [ ] **Step 4: Verify static checks**

Run:

```bash
node --check tradingagents/web/static/workbench.js
python3 -m py_compile tradingagents/web/api.py
python3 -m pytest tests/test_web_workbench_static.py tests/test_web_admin_api.py -v
```

Expected: JS and Python syntax checks exit 0; tests pass when pytest is available.

- [ ] **Step 5: Start local server**

Run:

```bash
tradingagents-web --host 127.0.0.1 --port 8000
```

Expected: Uvicorn reports `http://127.0.0.1:8000`.

- [ ] **Step 6: Run health and asset smoke checks**

Run:

```bash
curl -LsSf http://127.0.0.1:8000/healthz
curl -LsSf http://127.0.0.1:8000/assets/workbench.css
curl -LsSf http://127.0.0.1:8000/assets/workbench.js
```

Expected: health returns `{"ok":true}` and both assets return content.

- [ ] **Step 7: Browser smoke checklist**

Open `http://127.0.0.1:8000` and verify:

- Navigation switches between `个股分析`, `板块筛选`, `报告中心`, and `后台管理`.
- Saving an identity email updates the sidebar summary.
- Starting an analysis shows connecting/running state and disables the start button.
- Long timeline output is collapsed behind `details`.
- Reports appear in `报告中心` tabs as sections arrive.
- Sector screening shows loading, candidate table, AI review note, and report.
- `带入分析` switches to `个股分析` and fills the ticker without auto-starting.
- Admin login/setup appears in the modal.
- After admin login, `后台管理` shows model and whitelist forms.
- `/admin` opens the compatibility bridge and links to `/?view=admin`.

- [ ] **Step 8: Commit Task 7**

```bash
git add tradingagents/web/static/workbench.css tradingagents/web/static/workbench.js tests/test_web_workbench_static.py
git commit -m "polish: verify workbench responsive ui"
```

---

## Plan Self-Review

Spec coverage:

- App shell and navigation: Task 1 and Task 2.
- Login and identity: Task 2.
- Single-stock analysis: Task 3.
- Sector screening: Task 4.
- Report center: Task 5.
- Admin management: Task 6.
- Visual system, accessibility, responsiveness: Task 1 CSS foundations and Task 7 polish.
- `/admin` compatibility: Task 1.
- Existing backend and SSE preservation: Task 3 consumes `/api/events`; Task 6 consumes existing admin APIs; no graph changes are planned.

Planning marker scan:

- No banned planning markers or undefined task references are used.
- Every task has exact files, interfaces, commands, expected results, and commit scope.

Type and name consistency:

- `identityQuery`, `adminHeaders`, `refreshAdminStatus`, `showView`, and `setAnalysisTicker` are defined before tasks that consume them.
- `renderMarkdown` is defined before sector reports and report center depend on it in the final implementation order.
- `state.reports` is produced in Task 3 and consumed by Task 5.

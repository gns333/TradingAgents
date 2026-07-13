# 后台分析、报告隔离与模型目录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Web 分析改为可恢复展示的服务端后台任务，按用户隔离任务和报告，并为管理员提供官方模型目录获取能力。

**Architecture:** SQLite 继续作为当前持久化实现；`AdminStore` 保存任务、事件、报告和模型配置，独立的 `AnalysisTaskService` 负责后台执行。FastAPI 解析统一身份并实施对象级权限，前端通过任务创建 API 和可断点续传的 SSE 观察执行过程。模型目录由后端供应商适配器获取，快速与深度模型共用同一目录但保持独立选择。

**Tech Stack:** Python 3.10+、FastAPI、SQLite、ThreadPoolExecutor、requests、原生 JavaScript/CSS、pytest。

## Global Constraints

- 每个普通用户最多存在一个 `queued` 或 `running` 任务；不同用户可以并行。
- 普通用户只能访问自己的任务和报告；管理员可以访问全部数据。
- 报告只要求合法 Markdown，不统一章节，不增加 LLM 排版调用。
- 快速模型和深度模型保留现有 Agent 角色映射，并允许选择同一个模型。
- API Key 仅在后端使用，继续加密保存到数据库，不写入 URL、日志或响应。
- 不提交 `run.sh`、`.DS_Store`、本机文件或密钥。
- 按风险运行最小相关测试；只在集成检查点运行合并后的 Web 回归测试。

---

## File Structure

- `tradingagents/web/identity.py`：请求身份标准化和稳定 `owner_key` 生成。
- `tradingagents/web/admin_store.py`：SQLite 迁移、任务/事件仓储、报告归属查询、指定模型配置解密。
- `tradingagents/web/task_service.py`：后台线程池执行、事件持久化、报告落库和中断恢复。
- `tradingagents/web/model_catalog.py`：供应商官方模型列表适配、缓存和结构化错误。
- `tradingagents/web/api.py`：任务、报告和模型目录 API；对象级权限与兼容 SSE。
- `tradingagents/web/static/workbench.js`：任务创建/恢复、断线续传、报告用户视图、动态模型选择。
- `tradingagents/web/static/workbench.css`：任务状态、管理员报告列及空错误区域样式。
- `tradingagents/agents/utils/agent_utils.py`：共享 Markdown 输出指令。
- `tradingagents/agents/analysts/*.py`、`tradingagents/agents/managers/*.py`、`tradingagents/agents/trader/trader.py`：报告节点使用 Markdown 指令。
- `tests/test_web_admin_store.py`：任务唯一性、事件顺序、报告隔离和迁移测试。
- `tests/test_web_task_service.py`：后台执行生命周期测试。
- `tests/test_web_admin_api.py`、`tests/test_web_stock_and_reports.py`：身份、权限、任务和目录 API 测试。
- `tests/test_web_workbench_static.py`：前端任务恢复、模型目录和错误框契约测试。
- `tests/test_agent_language.py`：Markdown 指令覆盖测试。

---

### Task 1: 统一身份与持久化任务模型

**Files:**
- Create: `tradingagents/web/identity.py`
- Modify: `tradingagents/web/admin_store.py`
- Modify: `tests/test_web_admin_store.py`

**Interfaces:**
- Produces: `Principal(owner_key: str, uid: str, email: str, is_admin: bool)`。
- Produces: `AdminStore.create_analysis_run(payload) -> dict`、`claim_analysis_run(run_id) -> dict | None`、`append_analysis_event(run_id, event) -> dict`、`get_active_analysis_run(owner_key) -> dict | None`。
- Produces: `AdminStore.get_analysis_run(run_id) -> dict | None`、`mark_interrupted_runs_failed() -> int`。

- [ ] **Step 1: 写身份键和每用户活动任务唯一性的失败测试**

```python
from tradingagents.web.identity import Principal

def test_principal_prefers_uid_and_normalizes_email():
    principal = Principal.from_values(uid=" cb-123 ", email="User@Example.com")
    assert principal.owner_key == "uid:cb-123"
    assert principal.email == "user@example.com"

def test_only_one_active_run_is_allowed_per_owner(tmp_path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    first = store.create_analysis_run({
        "owner_key": "uid:u1", "ticker": "600519.SH", "trade_date": "2026-07-14",
        "analysts": ["market"],
    })
    with pytest.raises(ActiveRunExists) as exc:
        store.create_analysis_run({
            "owner_key": "uid:u1", "ticker": "000001.SZ", "trade_date": "2026-07-14",
            "analysts": ["market"],
        })
    assert exc.value.run["id"] == first["id"]
```

- [ ] **Step 2: 运行测试并确认因接口不存在而失败**

Run: `pytest tests/test_web_admin_store.py -k "principal or active_run" -q`

Expected: FAIL，提示 `identity`、`create_analysis_run` 或 `ActiveRunExists` 尚未定义。

- [ ] **Step 3: 实现身份对象、数据库迁移和任务状态操作**

```python
@dataclass(frozen=True)
class Principal:
    owner_key: str
    uid: str = ""
    email: str = ""
    is_admin: bool = False

    @classmethod
    def from_values(cls, uid="", email="", is_admin=False):
        clean_uid = str(uid or "").strip()
        clean_email = str(email or "").strip().lower()
        if is_admin:
            return cls("admin:local", clean_uid, clean_email, True)
        if clean_uid:
            return cls(f"uid:{clean_uid}", clean_uid, clean_email)
        if clean_email:
            return cls(f"email:{clean_email}", "", clean_email)
        raise IdentityRequired("A verified uid or email is required")
```

在 `_init_db()` 中增加 `analysis_runs`、`analysis_run_events`、索引以及 `analysis_reports` 的增量列迁移。`create_analysis_run()` 使用 `BEGIN IMMEDIATE` 检查活动任务后插入，遇到同用户活动任务抛出包含已有任务的 `ActiveRunExists`。

- [ ] **Step 4: 增加事件顺序、领取任务和中断恢复测试并实现**

```python
def test_events_are_monotonic_and_running_jobs_are_failed_on_recovery(tmp_path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    run = store.create_analysis_run({
        "owner_key": "uid:u1",
        "ticker": "600519.SH",
        "stock_name": "贵州茅台",
        "trade_date": "2026-07-14",
        "asset_type": "stock",
        "analysts": ["market"],
    })
    assert store.claim_analysis_run(run["id"])["status"] == "running"
    first = store.append_analysis_event(run["id"], AnalysisEvent("run_started", {}))
    second = store.append_analysis_event(run["id"], AnalysisEvent("agent_message", {"content": "x"}))
    assert [first["seq"], second["seq"]] == [1, 2]
    assert store.mark_interrupted_runs_failed() == 1
    assert store.get_analysis_run(run["id"])["error_type"] == "WorkerInterrupted"
```

Run: `pytest tests/test_web_admin_store.py -k "run or event or principal" -q`

Expected: PASS。

---

### Task 2: 后台任务执行服务

**Files:**
- Create: `tradingagents/web/task_service.py`
- Create: `tests/test_web_task_service.py`
- Modify: `tradingagents/web/runner.py`

**Interfaces:**
- Consumes: Task 1 的任务和事件仓储方法。
- Produces: `AnalysisTaskService.submit(run_id) -> Future`、`recover() -> None`、`shutdown() -> None`。

- [ ] **Step 1: 写页面连接无关的任务完成测试**

```python
def test_service_persists_events_and_report_without_sse_client(tmp_path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    run = store.create_analysis_run({
        "owner_key": "uid:u1",
        "owner_uid": "u1",
        "owner_email": "user@example.com",
        "ticker": "600519.SH",
        "stock_name": "贵州茅台",
        "trade_date": "2026-07-14",
        "asset_type": "stock",
        "analysts": ["market"],
    })
    events = [
        AnalysisEvent("run_started", {}),
        AnalysisEvent("report_section_updated", {"section": "market_report", "content": "# 市场"}),
        AnalysisEvent("run_completed", {"final_state": {}}),
    ]
    service = AnalysisTaskService(store, graph_builder=lambda request: object(), event_stream=lambda graph, request: events)
    service.submit(run["id"]).result(timeout=2)
    saved = store.get_analysis_run(run["id"])
    assert saved["status"] == "completed"
    assert saved["report_id"] is not None
```

- [ ] **Step 2: 运行测试并确认服务尚不存在**

Run: `pytest tests/test_web_task_service.py -q`

Expected: FAIL，提示 `AnalysisTaskService` 尚未定义。

- [ ] **Step 3: 实现有限线程池和事件落库**

```python
class AnalysisTaskService:
    def __init__(self, store, graph_builder=create_graph_for_request,
                 event_stream=stream_analysis_events, max_workers=2):
        self.store = store
        self.graph_builder = graph_builder
        self.event_stream = event_stream
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="analysis")

    def submit(self, run_id):
        return self.executor.submit(self._execute, run_id)
```

`_execute()` 先领取任务，再重建 `AnalysisRequest`，持久化每个事件；完成时保存报告名称、归属和 Markdown sections，失败时持久化真实错误。

- [ ] **Step 4: 写失败任务和重启恢复测试并实现**

```python
def test_service_marks_failed_event_as_terminal(tmp_path):
    # event_stream 返回 run_failed 后，任务必须为 failed 且不能保存报告。

def test_recover_fails_running_and_submits_queued(tmp_path):
    # recover() 标记遗留 running，并为 queued 调用 submit。
```

Run: `pytest tests/test_web_task_service.py -q`

Expected: PASS。

---

### Task 3: 任务 API、报告归属和管理员全局视图

**Files:**
- Modify: `tradingagents/web/api.py`
- Modify: `tradingagents/web/admin_store.py`
- Modify: `tests/test_web_admin_api.py`
- Modify: `tests/test_web_stock_and_reports.py`

**Interfaces:**
- Consumes: `Principal`、`AnalysisTaskService` 和 Task 1 仓储方法。
- Produces: `POST /api/runs`、`GET /api/runs/active`、`GET /api/runs/{id}`、`GET /api/runs/{id}/events`、`GET /api/runs/{id}/stream`。
- Produces: 身份隔离后的 `/api/reports`、`/api/reports/{id}`。

- [ ] **Step 1: 写每用户任务 API 和重复任务 409 测试**

```python
class FakeTaskService:
    def submit(self, run_id):
        return None

    def recover(self):
        return None

    def shutdown(self):
        return None


def test_create_run_returns_409_with_existing_run(tmp_path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    store.upsert_whitelist({"email": "user@example.com", "status": "active"})
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    monkeypatch.setattr(api, "create_task_service", lambda current_store: FakeTaskService())
    client = TestClient(api.create_app())
    payload = {"ticker": "600519.SH", "trade_date": "2026-07-14", "analysts": ["market"]}
    first = client.post("/api/runs?access_email=user@example.com", json=payload)
    second = client.post("/api/runs?access_email=user@example.com", json=payload)
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["run"]["id"] == first.json()["run"]["id"]
```

- [ ] **Step 2: 运行任务 API 测试并确认 404/失败**

Run: `pytest tests/test_web_admin_api.py -k "create_run or active_run" -q`

Expected: FAIL，因为路由尚未注册。

- [ ] **Step 3: 实现统一身份解析、任务路由和对象权限**

```python
def principal_from_request(request, access_email=None):
    is_admin = store.verify_admin_session(_admin_token(request))
    return Principal.from_values(
        uid=request.headers.get("x-cloudbase-uid") or request.headers.get("x-user-uid"),
        email=request.headers.get("x-cloudbase-email") or request.headers.get("x-user-email") or access_email,
        is_admin=is_admin,
    )
```

创建任务时解析股票目录名称并写入任务，提交给 Task Service。读取任务、事件和报告时，普通用户只能匹配 `owner_key`；越权统一返回 404。

- [ ] **Step 4: 写普通用户报告隔离和管理员全局可见测试**

```python
def test_reports_are_owner_scoped_and_admin_sees_all(tmp_path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    store.upsert_whitelist({"email": "a@example.com", "uid": "a", "status": "active"})
    report_a = store.save_analysis_report({
        "ticker": "600519.SH", "stock_name": "贵州茅台", "trade_date": "2026-07-14",
        "analysts": ["market"], "sections": {"market_report": "# A"},
        "owner_key": "uid:a", "owner_uid": "a", "owner_email": "a@example.com",
    })
    report_b = store.save_analysis_report({
        "ticker": "002594.SZ", "stock_name": "比亚迪", "trade_date": "2026-07-14",
        "analysts": ["market"], "sections": {"market_report": "# B"},
        "owner_key": "uid:b", "owner_uid": "b", "owner_email": "b@example.com",
    })
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    monkeypatch.setattr(api, "create_task_service", lambda current_store: FakeTaskService())
    client = TestClient(api.create_app())
    user_headers = {"x-user-uid": "a", "x-user-email": "a@example.com"}
    listing = client.get("/api/reports", headers=user_headers).json()["items"]
    assert [item["id"] for item in listing] == [report_a["id"]]
    assert client.get(f"/api/reports/{report_b['id']}", headers=user_headers).status_code == 404
    token = store.create_admin_session()
    admin_headers = {"Authorization": f"Bearer {token}"}
    assert len(client.get("/api/reports", headers=admin_headers).json()["items"]) == 2
```

- [ ] **Step 5: 实现报告迁移、中文名称和过滤查询并运行相关测试**

Run: `pytest tests/test_web_admin_api.py tests/test_web_stock_and_reports.py -q`

Expected: PASS。

---

### Task 4: 官方模型目录服务

**Files:**
- Create: `tradingagents/web/model_catalog.py`
- Create: `tests/test_web_model_catalog.py`
- Modify: `tradingagents/web/admin_store.py`
- Modify: `tradingagents/web/api.py`
- Modify: `tests/test_web_admin_api.py`

**Interfaces:**
- Produces: `ModelCatalog.fetch(provider, api_key, base_url=None) -> list[ModelInfo]`。
- Produces: `POST /api/admin/model-catalog`，接受临时 `api_key` 或已保存的 `config_id`。

- [ ] **Step 1: 写 OpenAI 兼容、Anthropic 和 Gemini 响应解析失败测试**

```python
def test_openai_compatible_catalog_uses_models_endpoint(fake_session):
    fake_session.add_json("https://api.deepseek.com/models", {"data": [{"id": "deepseek-v4-flash"}]})
    models = ModelCatalog(session=fake_session).fetch("deepseek", "sk-test")
    assert [model.id for model in models] == ["deepseek-v4-flash"]

def test_gemini_filters_non_generation_models(fake_session):
    fake_session.add_json(GEMINI_MODELS_URL, {"models": [
        {"name": "models/gemini-x", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/embed-x", "supportedGenerationMethods": ["embedContent"]},
    ]})
    assert [m.id for m in ModelCatalog(session=fake_session).fetch("google", "key")] == ["gemini-x"]
```

- [ ] **Step 2: 运行测试并确认模块不存在**

Run: `pytest tests/test_web_model_catalog.py -q`

Expected: FAIL，提示 `model_catalog` 尚未定义。

- [ ] **Step 3: 实现供应商端点、认证头、解析和五分钟缓存**

```python
PROVIDER_ENDPOINTS = {
    "deepseek": "https://api.deepseek.com/models",
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "google": "https://generativelanguage.googleapis.com/v1beta/models",
    "kimi": "https://api.moonshot.cn/v1/models",
}
```

缓存键使用 `sha256(api_key)` 指纹；不保存明文 Key。百炼和智谱抛出 `CatalogUnsupported`，由 API 返回可手动输入的结构化结果。

- [ ] **Step 4: 写管理员权限、临时 Key 不回显和已保存配置解密测试**

```python
class FakeModelCatalog:
    def __init__(self, model_ids):
        self.model_ids = model_ids

    def fetch(self, provider, api_key, base_url=None):
        return [ModelInfo(id=model_id, display_name=model_id) for model_id in self.model_ids]


def test_model_catalog_requires_admin_and_never_echoes_key(tmp_path, monkeypatch):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.set_admin_password("correct-horse")
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    monkeypatch.setattr(api, "get_model_catalog", lambda: FakeModelCatalog(["deepseek-v4-flash"]))
    client = TestClient(api.create_app())
    payload = {"provider": "deepseek", "api_key": "sk-secret", "base_url": "https://api.deepseek.com"}
    assert client.post("/api/admin/model-catalog", json=payload).status_code == 401
    token = store.create_admin_session()
    response = client.post(
        "/api/admin/model-catalog",
        headers={"Authorization": f"Bearer {token}"},
        json={"provider": "deepseek", "api_key": "sk-secret"},
    )
    assert "sk-secret" not in response.text
```

- [ ] **Step 5: 实现目录 API 并运行相关测试**

Run: `pytest tests/test_web_model_catalog.py tests/test_web_admin_api.py -k "model" -q`

Expected: PASS。

---

### Task 5: 前端后台任务恢复、报告标题和管理员体验

**Files:**
- Modify: `tradingagents/web/static/workbench.js`
- Modify: `tradingagents/web/static/workbench.css`
- Modify: `tests/test_web_workbench_static.py`

**Interfaces:**
- Consumes: Task 3 和 Task 4 的 API。
- Produces: 页面刷新后恢复活动任务、增量 SSE 重连、按权限加载报告、动态模型下拉候选。

- [ ] **Step 1: 写新的前端契约失败测试**

```python
def test_workbench_uses_durable_runs_and_dynamic_model_catalog():
    js = (STATIC_DIR / "workbench.js").read_text()
    css = (STATIC_DIR / "workbench.css").read_text()
    assert "async function restoreActiveRun()" in js
    assert "POST" in js and "/api/runs" in js
    assert "/api/admin/model-catalog" in js
    assert "quick_models:" not in js
    assert "deep_models:" not in js
    assert ".form-status:empty" in css
```

- [ ] **Step 2: 运行静态测试并确认旧实现不满足契约**

Run: `pytest tests/test_web_workbench_static.py -q`

Expected: FAIL，缺少任务恢复和动态目录契约。

- [ ] **Step 3: 改造任务状态和启动流程**

```javascript
const state = {
  activeRunId: '',
  lastEventSeq: 0,
  reconnectTimer: null
};

async function startAnalysis() {
  const result = await apiJson(`/api/runs?${identityQuery()}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
  });
  await attachToRun(result.run);
}
```

`restoreActiveRun()` 在工作台初始化和身份改变后运行。SSE 错误只显示“正在重连”，不启用提交按钮；使用 `after=lastEventSeq` 补齐事件。

- [ ] **Step 4: 改造报告列表和标题**

普通用户请求自动带身份，管理员保持全局列表。标题渲染使用：

```javascript
function reportInstrumentLabel(report) {
  return report.stock_name ? `${report.stock_name}（${report.ticker}）` : report.ticker;
}
```

管理员列表额外显示 `owner_email || owner_uid || '历史未归属'`。

- [ ] **Step 5: 改造模型配置为同一动态目录的两个独立输入**

供应商预设只保留 `label`、`base_url`、`catalog_supported`。点击“获取模型”后填充同一个 datalist，快速和深度输入都引用它，并允许手动保留任意模型 ID。

- [ ] **Step 6: 隐藏空管理员错误框并运行静态测试**

```css
.form-status:empty {
  display: none;
}
```

Run: `pytest tests/test_web_workbench_static.py tests/test_web_static.py -q`

Expected: PASS。

---

### Task 6: 保证所有保存报告使用 Markdown

**Files:**
- Modify: `tradingagents/agents/utils/agent_utils.py`
- Modify: `tradingagents/agents/analysts/market_analyst.py`
- Modify: `tradingagents/agents/analysts/news_analyst.py`
- Modify: `tradingagents/agents/analysts/fundamentals_analyst.py`
- Modify: `tradingagents/agents/analysts/sentiment_analyst.py`
- Modify: `tradingagents/agents/managers/research_manager.py`
- Modify: `tradingagents/agents/managers/portfolio_manager.py`
- Modify: `tradingagents/agents/trader/trader.py`
- Modify: `tests/test_agent_language.py`
- Modify: `tests/test_web_workbench_static.py`

**Interfaces:**
- Produces: `get_report_format_instruction() -> str`。
- Preserves: 各报告原有领域结构和结构化输出字段。

- [ ] **Step 1: 写 Markdown 指令测试**

```python
def test_report_format_instruction_requires_markdown_without_fixed_sections():
    instruction = get_report_format_instruction()
    assert "Markdown" in instruction
    assert "required section" not in instruction.lower()
```

- [ ] **Step 2: 运行测试并确认 helper 尚不存在**

Run: `pytest tests/test_agent_language.py -k markdown -q`

Expected: FAIL，提示 `get_report_format_instruction` 尚未定义。

- [ ] **Step 3: 实现并接入所有会保存为报告的节点**

```python
def get_report_format_instruction() -> str:
    return (
        " Format the complete response as valid Markdown. Use headings, lists, "
        "tables, quotes, or code blocks only when they improve clarity; no fixed "
        "section names or section order are required."
    )
```

结构化 Agent 的确定性渲染器保持现状；只为自由文本 fallback 补充相同指令，不改变 schema 字段。

- [ ] **Step 4: 加固前端 Markdown HTML 转义回退并运行测试**

确保 `renderMarkdown('<script>alert(1)</script>')` 返回转义文本，解析异常时也显示原始 Markdown，而不是插入 HTML。

Run: `pytest tests/test_agent_language.py tests/test_web_workbench_static.py -q`

Expected: PASS。

---

### Task 7: 集成回归、文档同步与提交

**Files:**
- Modify: `README.md`
- Verify: all files changed by Tasks 1-6

**Interfaces:**
- Consumes: 所有前序任务。
- Produces: 可本地启动、可验证的完整后台任务流程。

- [ ] **Step 1: 检查迁移后的 API 路由和未提交文件范围**

Run: `git status --short && rg -n "api/runs|model-catalog|owner_key" tradingagents/web tests`

Expected: 只包含本功能文件、已有行情修复和未跟踪 `run.sh`；`run.sh` 不进入暂存区。

- [ ] **Step 2: 运行合并后的 Web 和报告回归测试**

Run: `pytest tests/test_web_admin_store.py tests/test_web_task_service.py tests/test_web_model_catalog.py tests/test_web_admin_api.py tests/test_web_events.py tests/test_web_runner.py tests/test_web_stock_and_reports.py tests/test_web_static.py tests/test_web_workbench_static.py tests/test_agent_language.py tests/test_reporting.py -q`

Expected: PASS，无未处理线程异常或网络请求。

- [ ] **Step 3: 运行格式和语法检查**

Run: `python -m compileall -q tradingagents/web tradingagents/agents`

Expected: exit 0。

Run: `git diff --check`

Expected: exit 0。

- [ ] **Step 4: 启动本地 Web 服务并执行最小浏览器冒烟检查**

Run: `tradingagents-web --host 127.0.0.1 --port 8010`

然后使用浏览器检查登录弹窗、任务恢复区域、报告中文名称、模型目录控件和移动端无重叠。

Expected: 页面无控制台错误；关闭并重新打开页面后，伪造或测试任务状态能够恢复。

- [ ] **Step 5: 只暂存本功能文件并提交**

```bash
git add docs/superpowers/plans/2026-07-14-background-analysis-and-model-catalog.md \
  tradingagents/web tradingagents/agents tests README.md
git status --short
git commit -m "Add durable per-user analysis tasks"
```

提交前逐项排除 `run.sh`、`.DS_Store`、密钥，以及本功能之外的 `market_data_validator.py` 现有修改。

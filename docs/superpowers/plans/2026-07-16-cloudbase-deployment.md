# CloudBase Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the existing TradingAgents Web workbench to CloudBase from GitHub while preserving zero-database local operation, adding CloudBase Auth/MySQL, encrypted administrator-managed model keys, and database-controlled dynamic task concurrency.

**Architecture:** Keep the existing FastAPI API and local SQLite implementation as the compatibility baseline. Add explicit runtime configuration, a storage protocol with a CloudBase MySQL implementation, pluggable local/CloudBase identity providers, and a single-process database-backed scheduler that reads its concurrency settings from the application database. CloudBase runs one Uvicorn process in one container instance; GitHub pushes trigger Dockerfile builds.

**Tech Stack:** Python 3.10+, FastAPI, Uvicorn, SQLite, PyMySQL, CloudBase MySQL, CloudBase Web SDK v2, AES-GCM, pytest, Docker, GitHub Actions.

## Global Constraints

- Local mode remains the default and must start without MySQL or a CloudBase account.
- CloudBase mode must be selected explicitly with `TRADINGAGENTS_RUNTIME=cloudbase`; it must never silently fall back to SQLite.
- Model-provider API keys are entered only through the Web administrator UI and are never deployment environment variables.
- CloudBase mode requires a separate `TRADINGAGENTS_MASTER_KEY`; this is an encryption root key, not a model-provider API key.
- Task concurrency and queue limits are stored only in `app_settings`; do not add environment-variable overrides for them.
- Default runtime settings are concurrency `2`, queued-task limit `20`, and accepting new tasks `true`.
- Administrator concurrency values are restricted to `1..8`; queue limits are restricted to `1..200`.
- Lowering concurrency never cancels a running analysis.
- Each ordinary user may own at most one `queued` or `running` analysis.
- CloudBase mode trusts only gateway-validated `x-cloudbase-context`; browser-supplied UID, email, or role fields are not identities.
- CloudBase production runs exactly one instance and one Uvicorn worker for this phase.
- A queued task survives process restart; an interrupted running task becomes `failed` with `WorkerInterrupted`.
- Preserve unrelated user changes and do not refactor Agent graph or report-generation behavior.

---

### Task 1: Explicit Web Runtime Configuration

**Files:**
- Create: `tradingagents/web/runtime.py`
- Create: `tests/test_web_runtime.py`
- Modify: `pyproject.toml`
- Modify: `tradingagents/web/cli.py`

**Interfaces:**
- Produces: `WebRuntimeConfig`, `RuntimeConfigurationError`, `load_web_runtime_config()`.
- `WebRuntimeConfig.mode` is exactly `"local"` or `"cloudbase"`.
- `WebRuntimeConfig.master_key` is `bytes | None`; CloudBase values decode from URL-safe base64 and must be 32 bytes.
- `tradingagents-web` consumes `PORT` only for the HTTP listen port; it does not consume task-concurrency environment variables.

- [ ] **Step 1: Write failing runtime-configuration tests**

```python
# tests/test_web_runtime.py
import base64

import pytest

from tradingagents.web.runtime import (
    RuntimeConfigurationError,
    load_web_runtime_config,
)


def test_local_runtime_is_the_zero_configuration_default():
    config = load_web_runtime_config({})

    assert config.mode == "local"
    assert config.database_url.endswith(".tradingagents/web_admin.sqlite3")
    assert config.cloudbase_env_id == ""
    assert config.master_key is None


def test_cloudbase_runtime_requires_database_auth_and_master_key():
    with pytest.raises(RuntimeConfigurationError) as exc_info:
        load_web_runtime_config({"TRADINGAGENTS_RUNTIME": "cloudbase"})

    assert "TRADINGAGENTS_DATABASE_URL" in str(exc_info.value)
    assert "TRADINGAGENTS_MASTER_KEY" in str(exc_info.value)


def test_cloudbase_runtime_decodes_a_32_byte_master_key():
    encoded = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
    config = load_web_runtime_config(
        {
            "TRADINGAGENTS_RUNTIME": "cloudbase",
            "TRADINGAGENTS_DATABASE_URL": "mysql+pymysql://user:pass@db/tcb",
            "TRADINGAGENTS_CLOUDBASE_ENV_ID": "env-123",
            "TRADINGAGENTS_CLOUDBASE_REGION": "ap-shanghai",
            "TRADINGAGENTS_CLOUDBASE_PUBLISHABLE_KEY": "public-key",
            "TRADINGAGENTS_MASTER_KEY": encoded,
        }
    )

    assert config.mode == "cloudbase"
    assert config.master_key == b"k" * 32
    assert config.cloudbase_publishable_key == "public-key"


def test_runtime_rejects_task_concurrency_environment_variables():
    config = load_web_runtime_config(
        {"TRADINGAGENTS_MAX_CONCURRENT_TASKS": "7"}
    )

    assert not hasattr(config, "max_concurrent_tasks")
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```bash
pytest tests/test_web_runtime.py -v
```

Expected: collection fails because `tradingagents.web.runtime` does not exist.

- [ ] **Step 3: Implement immutable runtime configuration**

```python
# tradingagents/web/runtime.py
from __future__ import annotations

import base64
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


class RuntimeConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class WebRuntimeConfig:
    mode: str
    database_url: str
    cloudbase_env_id: str = ""
    cloudbase_region: str = "ap-shanghai"
    cloudbase_publishable_key: str = ""
    master_key: bytes | None = None


def _required(env: Mapping[str, str], name: str, missing: list[str]) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        missing.append(name)
    return value


def load_web_runtime_config(
    environ: Mapping[str, str] | None = None,
) -> WebRuntimeConfig:
    env = os.environ if environ is None else environ
    mode = str(env.get("TRADINGAGENTS_RUNTIME") or "local").strip().lower()
    if mode == "local":
        db_path = Path(".tradingagents") / "web_admin.sqlite3"
        return WebRuntimeConfig(
            mode="local",
            database_url=f"sqlite:///{db_path.as_posix()}",
        )
    if mode != "cloudbase":
        raise RuntimeConfigurationError(
            "TRADINGAGENTS_RUNTIME must be 'local' or 'cloudbase'"
        )

    missing: list[str] = []
    database_url = _required(env, "TRADINGAGENTS_DATABASE_URL", missing)
    env_id = _required(env, "TRADINGAGENTS_CLOUDBASE_ENV_ID", missing)
    publishable_key = _required(
        env, "TRADINGAGENTS_CLOUDBASE_PUBLISHABLE_KEY", missing
    )
    encoded_key = _required(env, "TRADINGAGENTS_MASTER_KEY", missing)
    if missing:
        raise RuntimeConfigurationError(
            "CloudBase runtime is missing: " + ", ".join(missing)
        )
    try:
        master_key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeConfigurationError(
            "TRADINGAGENTS_MASTER_KEY must be URL-safe base64"
        ) from exc
    if len(master_key) != 32:
        raise RuntimeConfigurationError(
            "TRADINGAGENTS_MASTER_KEY must decode to exactly 32 bytes"
        )

    return WebRuntimeConfig(
        mode="cloudbase",
        database_url=database_url,
        cloudbase_env_id=env_id,
        cloudbase_region=str(
            env.get("TRADINGAGENTS_CLOUDBASE_REGION") or "ap-shanghai"
        ).strip(),
        cloudbase_publishable_key=publishable_key,
        master_key=master_key,
    )
```

- [ ] **Step 4: Make the Web CLI honor CloudBase `PORT`**

```python
# tradingagents/web/cli.py
import os

# Replace the fixed default:
parser.add_argument(
    "--port",
    type=int,
    default=int(os.getenv("PORT", "8000")),
)
```

Do not change `--host` default in this task; Docker supplies `0.0.0.0`.

- [ ] **Step 5: Add the CloudBase MySQL driver as an optional extra**

```toml
# pyproject.toml
[project.optional-dependencies]
cloudbase = [
    "pymysql>=1.1",
]
```

Keep the existing `web`, `china`, and other extras unchanged.

- [ ] **Step 6: Run focused tests**

Run:

```bash
pytest tests/test_web_runtime.py tests/test_web_runner.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit the runtime configuration**

```bash
git add pyproject.toml tradingagents/web/runtime.py tradingagents/web/cli.py tests/test_web_runtime.py
git commit -m "feat: add explicit web runtime modes"
```

---

### Task 2: Storage Contract and Database-Backed Runtime Settings

**Files:**
- Create: `tradingagents/web/store.py`
- Modify: `tradingagents/web/admin_store.py`
- Modify: `tests/test_web_admin_store.py`

**Interfaces:**
- Produces: `ApplicationStore` protocol, `RuntimeSettings`, `QueueLimitReached`, `TaskSubmissionPaused`.
- Produces store methods:
  - `get_runtime_settings() -> RuntimeSettings`
  - `update_runtime_settings(payload: dict[str, object], updated_by: str) -> RuntimeSettings`
  - `count_queued_analysis_runs() -> int`
  - `count_running_analysis_runs() -> int`
  - `claim_next_analysis_run() -> dict[str, object] | None`
- Existing `AdminStore` public methods remain compatible for local callers.

- [ ] **Step 1: Add failing SQLite settings and admission tests**

```python
# append to tests/test_web_admin_store.py
from tradingagents.web.store import (
    QueueLimitReached,
    TaskSubmissionPaused,
)


def test_runtime_settings_have_database_defaults_and_validate_updates(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")

    defaults = store.get_runtime_settings()
    assert defaults.analysis_concurrency_limit == 2
    assert defaults.analysis_queue_limit == 20
    assert defaults.accept_new_tasks is True
    assert defaults.warning == ""

    updated = store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 4,
            "analysis_queue_limit": 30,
            "accept_new_tasks": False,
        },
        updated_by="uid:admin",
    )
    assert updated.analysis_concurrency_limit == 4
    assert updated.analysis_queue_limit == 30
    assert updated.accept_new_tasks is False

    with pytest.raises(ValueError, match="1 and 8"):
        store.update_runtime_settings(
            {
                "analysis_concurrency_limit": 9,
                "analysis_queue_limit": 30,
                "accept_new_tasks": True,
            },
            updated_by="uid:admin",
        )


def test_paused_submission_and_queue_limit_are_enforced_in_store(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 1,
            "analysis_queue_limit": 1,
            "accept_new_tasks": False,
        },
        updated_by="uid:admin",
    )

    with pytest.raises(TaskSubmissionPaused):
        store.create_analysis_run(
            {
                "owner_key": "uid:u1",
                "ticker": "600519.SH",
                "trade_date": "2026-07-16",
                "analysts": ["market"],
            }
        )

    store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 1,
            "analysis_queue_limit": 1,
            "accept_new_tasks": True,
        },
        updated_by="uid:admin",
    )
    store.create_analysis_run(
        {
            "owner_key": "uid:u1",
            "ticker": "600519.SH",
            "trade_date": "2026-07-16",
            "analysts": ["market"],
        }
    )
    with pytest.raises(QueueLimitReached):
        store.create_analysis_run(
            {
                "owner_key": "uid:u2",
                "ticker": "000001.SZ",
                "trade_date": "2026-07-16",
                "analysts": ["market"],
            }
        )


def test_claim_next_analysis_run_is_fifo(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    first = store.create_analysis_run(
        {
            "owner_key": "uid:u1",
            "ticker": "600519.SH",
            "trade_date": "2026-07-16",
            "analysts": ["market"],
        }
    )
    store.create_analysis_run(
        {
            "owner_key": "uid:u2",
            "ticker": "000001.SZ",
            "trade_date": "2026-07-16",
            "analysts": ["market"],
        }
    )

    claimed = store.claim_next_analysis_run()

    assert claimed["id"] == first["id"]
    assert claimed["status"] == "running"
    assert store.count_running_analysis_runs() == 1
    assert store.count_queued_analysis_runs() == 1
```

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
pytest tests/test_web_admin_store.py -v
```

Expected: import or attribute failures for the new store contract and methods.

- [ ] **Step 3: Define the shared contract and validated settings**

```python
# tradingagents/web/store.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class QueueLimitReached(RuntimeError):
    pass


class TaskSubmissionPaused(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeSettings:
    analysis_concurrency_limit: int = 2
    analysis_queue_limit: int = 20
    accept_new_tasks: bool = True
    warning: str = ""
    updated_by: str = ""
    updated_at: str = ""

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        updated_by: str = "",
        updated_at: str = "",
    ) -> "RuntimeSettings":
        concurrency = int(payload["analysis_concurrency_limit"])
        queue_limit = int(payload["analysis_queue_limit"])
        accepting = payload["accept_new_tasks"]
        if not 1 <= concurrency <= 8:
            raise ValueError("analysis_concurrency_limit must be between 1 and 8")
        if not 1 <= queue_limit <= 200:
            raise ValueError("analysis_queue_limit must be between 1 and 200")
        if not isinstance(accepting, bool):
            raise ValueError("accept_new_tasks must be a boolean")
        return cls(
            concurrency,
            queue_limit,
            accepting,
            updated_by=updated_by,
            updated_at=updated_at,
        )


class ApplicationStore(Protocol):
    def get_runtime_settings(self) -> RuntimeSettings: ...
    def update_runtime_settings(
        self, payload: dict[str, Any], updated_by: str
    ) -> RuntimeSettings: ...
    def count_queued_analysis_runs(self) -> int: ...
    def count_running_analysis_runs(self) -> int: ...
    def claim_next_analysis_run(self) -> dict[str, Any] | None: ...
```

The protocol must also declare every existing method consumed by `api.py`, `runner.py`, and `task_service.py`; copy their current method names and return shapes exactly rather than renaming them.

- [ ] **Step 4: Seed settings in SQLite and read invalid values safely**

In `AdminStore._init_db()`, seed the three keys only when absent:

```python
defaults = {
    "analysis_concurrency_limit": "2",
    "analysis_queue_limit": "20",
    "accept_new_tasks": "true",
}
for key, value in defaults.items():
    if self._get_setting(conn, key) is None:
        self._set_setting(conn, key, value)
```

Implement `get_runtime_settings()` so malformed stored values return:

```python
RuntimeSettings(
    analysis_concurrency_limit=1,
    analysis_queue_limit=20,
    accept_new_tasks=True,
    warning="Stored runtime settings were invalid; concurrency was reduced to 1.",
)
```

Do not overwrite malformed values during the read; the administrator must save a corrected configuration.

- [ ] **Step 5: Enforce pause and queue limit atomically**

Update `AdminStore.create_analysis_run()` to use a write transaction before checking:

```python
conn.execute("BEGIN IMMEDIATE")
settings = self._runtime_settings_from_connection(conn)
if not settings.accept_new_tasks:
    raise TaskSubmissionPaused("new analysis submissions are paused")
queued = conn.execute(
    "SELECT COUNT(*) FROM analysis_runs WHERE status = 'queued'"
).fetchone()[0]
if queued >= settings.analysis_queue_limit:
    raise QueueLimitReached("analysis queue limit reached")
```

Perform the existing active-owner check and insert within that same transaction. Roll back before raising any admission exception.

- [ ] **Step 6: Implement FIFO claiming and counts**

`claim_next_analysis_run()` must:

1. begin `BEGIN IMMEDIATE`;
2. select the oldest `queued` row by `created_at, id`;
3. update it to `running`, setting `started_at` and `heartbeat_at`;
4. return the updated row;
5. return `None` if no queued row exists.

Keep `claim_analysis_run(run_id)` for compatibility with existing tests and direct recovery checks.

- [ ] **Step 7: Run the local storage regression set**

Run:

```bash
pytest tests/test_web_admin_store.py tests/test_web_stock_and_reports.py tests/test_web_admin_api.py -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit SQLite settings and store contract**

```bash
git add tradingagents/web/store.py tradingagents/web/admin_store.py tests/test_web_admin_store.py
git commit -m "feat: persist task runtime settings"
```

---

### Task 3: Dynamic Single-Process Task Scheduler

**Files:**
- Modify: `tradingagents/web/task_service.py`
- Modify: `tests/test_web_task_service.py`

**Interfaces:**
- Consumes: `ApplicationStore.get_runtime_settings()`, `claim_next_analysis_run()`, and count methods from Task 2.
- Produces:
  - `AnalysisTaskService.start() -> None`
  - `AnalysisTaskService.notify() -> None`
  - `AnalysisTaskService.dispatch_once() -> int`
  - `AnalysisTaskService.shutdown(wait: bool = True) -> None`
- The executor hard ceiling is 8; the database setting is the effective limit.

- [ ] **Step 1: Replace direct-submit tests with deterministic dispatcher tests**

Add tests using `threading.Event` so concurrency is observable without external APIs:

```python
# tests/test_web_task_service.py
import threading
import time


def test_dispatcher_obeys_dynamic_concurrency_and_keeps_excess_queued(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 1,
            "analysis_queue_limit": 20,
            "accept_new_tasks": True,
        },
        updated_by="uid:admin",
    )
    first = store.create_analysis_run(_run_payload("uid:u1"))
    second = store.create_analysis_run(_run_payload("uid:u2"))
    release = threading.Event()
    started: list[str] = []

    def event_stream(graph, request):
        started.append(request.ticker)
        release.wait(timeout=2)
        yield AnalysisEvent("run_completed", {})

    service = AnalysisTaskService(
        store,
        graph_builder=lambda request: object(),
        event_stream=event_stream,
    )
    service.start()
    service.notify()

    deadline = time.time() + 2
    while store.count_running_analysis_runs() != 1 and time.time() < deadline:
        time.sleep(0.01)

    assert store.count_running_analysis_runs() == 1
    assert store.count_queued_analysis_runs() == 1
    assert len(started) == 1

    store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 2,
            "analysis_queue_limit": 20,
            "accept_new_tasks": True,
        },
        updated_by="uid:admin",
    )
    service.notify()
    deadline = time.time() + 2
    while store.count_running_analysis_runs() != 2 and time.time() < deadline:
        time.sleep(0.01)

    assert store.count_running_analysis_runs() == 2
    assert store.count_queued_analysis_runs() == 0
    release.set()
    service.shutdown()
    assert store.get_analysis_run(first["id"])["status"] == "completed"
    assert store.get_analysis_run(second["id"])["status"] == "completed"


def test_lowering_limit_does_not_cancel_running_tasks(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 2,
            "analysis_queue_limit": 20,
            "accept_new_tasks": True,
        },
        updated_by="uid:admin",
    )
    runs = [
        store.create_analysis_run(_run_payload(f"uid:u{index}"))
        for index in range(1, 4)
    ]
    release = threading.Event()

    def event_stream(graph, request):
        release.wait(timeout=2)
        yield AnalysisEvent("run_completed", {})

    service = AnalysisTaskService(
        store,
        graph_builder=lambda request: object(),
        event_stream=event_stream,
    )
    service.start()
    service.notify()
    deadline = time.time() + 2
    while store.count_running_analysis_runs() != 2 and time.time() < deadline:
        time.sleep(0.01)

    store.update_runtime_settings(
        {
            "analysis_concurrency_limit": 1,
            "analysis_queue_limit": 20,
            "accept_new_tasks": True,
        },
        updated_by="uid:admin",
    )
    service.notify()

    assert store.count_running_analysis_runs() == 2
    assert store.count_queued_analysis_runs() == 1
    release.set()
    service.shutdown()
    assert all(store.get_analysis_run(run["id"])["status"] == "completed" for run in runs)
```

- [ ] **Step 2: Run scheduler tests and verify the old service fails them**

Run:

```bash
pytest tests/test_web_task_service.py -v
```

Expected: failures because `start`, `notify`, and database-driven dispatch do not exist.

- [ ] **Step 3: Implement the dispatcher lifecycle**

Refactor `AnalysisTaskService` around:

```python
MAX_EXECUTOR_WORKERS = 8


class AnalysisTaskService:
    def __init__(...):
        self.executor = ThreadPoolExecutor(
            max_workers=MAX_EXECUTOR_WORKERS,
            thread_name_prefix="analysis",
        )
        self._condition = threading.Condition()
        self._active: dict[str, Future] = {}
        self._stopping = False
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop,
            name="analysis-dispatcher",
            daemon=True,
        )

    def start(self) -> None:
        if not self._dispatcher.is_alive():
            self.store.mark_interrupted_runs_failed()
            self._dispatcher.start()
            self.notify()

    def notify(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def dispatch_once(self) -> int:
        settings = self.store.get_runtime_settings()
        capacity = max(
            0,
            settings.analysis_concurrency_limit - len(self._active),
        )
        submitted = 0
        for _ in range(capacity):
            run = self.store.claim_next_analysis_run()
            if run is None:
                break
            future = self.executor.submit(self._execute_claimed, run)
            self._active[str(run["id"])] = future
            future.add_done_callback(
                lambda finished, run_id=str(run["id"]): self._finished(
                    run_id, finished
                )
            )
            submitted += 1
        return submitted
```

`_execute_claimed(run)` must not claim the run again. It builds `AnalysisRequest` from the already-claimed row and keeps the existing event/report persistence behavior.

- [ ] **Step 4: Implement wait, completion notification, and shutdown**

```python
def _dispatch_loop(self) -> None:
    while True:
        with self._condition:
            if self._stopping:
                return
        submitted = self.dispatch_once()
        with self._condition:
            if self._stopping:
                return
            self._condition.wait(timeout=0.5 if submitted == 0 else 0.05)


def _finished(self, run_id: str, future: Future) -> None:
    with self._condition:
        self._active.pop(run_id, None)
        self._condition.notify_all()


def shutdown(self, wait: bool = True) -> None:
    with self._condition:
        self._stopping = True
        self._condition.notify_all()
    if self._dispatcher.is_alive():
        self._dispatcher.join(timeout=5)
    self.executor.shutdown(wait=wait, cancel_futures=False)
```

If `_execute_claimed` raises before persisting a terminal state, preserve the existing `run_failed` behavior.

- [ ] **Step 5: Keep a narrow compatibility shim**

Retain:

```python
def submit(self, run_id: str):
    self.notify()
    return self._active.get(str(run_id))


def recover(self):
    self.start()
    return []
```

Update tests and API callers so they no longer depend on `submit(...).result()`.

- [ ] **Step 6: Run scheduler and API regression tests**

Run:

```bash
pytest tests/test_web_task_service.py tests/test_web_admin_api.py tests/test_web_events.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit the scheduler**

```bash
git add tradingagents/web/task_service.py tests/test_web_task_service.py tests/test_web_admin_api.py
git commit -m "feat: add database-driven task scheduler"
```

---

### Task 4: CloudBase MySQL Store and Versioned Schema

**Files:**
- Create: `tradingagents/web/mysql_migrations.py`
- Create: `tradingagents/web/mysql_store.py`
- Create: `tradingagents/web/store_factory.py`
- Create: `tests/test_web_store_contract.py`
- Create: `tests/test_web_mysql_store.py`
- Modify: `tradingagents/web/admin_store.py`
- Modify: `tradingagents/web/runner.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `WebRuntimeConfig` and `ApplicationStore`.
- Produces:
  - `MySQLApplicationStore(database_url: str, master_key: bytes)`
  - `create_application_store(config: WebRuntimeConfig) -> ApplicationStore`
  - `get_application_store() -> ApplicationStore`
- Local compatibility export `AdminStore` remains available.

- [ ] **Step 1: Extract backend-neutral store contract tests**

```python
# tests/test_web_store_contract.py
from tradingagents.web.events import AnalysisEvent


def assert_store_contract(store):
    settings = store.get_runtime_settings()
    assert settings.analysis_concurrency_limit == 2

    store.upsert_whitelist({"email": "user@example.com", "status": "active"})
    assert store.is_identity_allowed(email="user@example.com")

    model = store.save_model_config(
        {
            "provider": "deepseek",
            "display_name": "DeepSeek",
            "quick_model": "quick",
            "deep_model": "deep",
            "api_key": "sk-contract-secret",
            "is_default": True,
        }
    )
    assert "sk-contract-secret" not in str(model)
    assert store.get_default_runtime_model().api_key == "sk-contract-secret"

    run = store.create_analysis_run(
        {
            "owner_key": "uid:u1",
            "owner_uid": "u1",
            "ticker": "600519.SH",
            "trade_date": "2026-07-16",
            "analysts": ["market"],
        }
    )
    claimed = store.claim_next_analysis_run()
    assert claimed["id"] == run["id"]
    event = store.append_analysis_event(
        run["id"], AnalysisEvent("run_started", {})
    )
    assert event["seq"] == 1
    store.fail_analysis_run(run["id"], "ContractFailure", "expected")
    assert store.get_analysis_run(run["id"])["status"] == "failed"
```

Call this helper from the existing SQLite test suite and the new MySQL integration test.

- [ ] **Step 2: Add a failing MySQL integration test**

```python
# tests/test_web_mysql_store.py
import base64
import os

import pytest

from tests.test_web_store_contract import assert_store_contract
from tradingagents.web.mysql_store import MySQLApplicationStore


@pytest.mark.integration
def test_mysql_store_matches_application_store_contract():
    database_url = os.environ.get("TRADINGAGENTS_TEST_MYSQL_URL")
    if not database_url:
        pytest.skip("TRADINGAGENTS_TEST_MYSQL_URL is not configured")
    store = MySQLApplicationStore(database_url, b"m" * 32)
    store.reset_test_data()

    assert_store_contract(store)
```

- [ ] **Step 3: Run the MySQL test and verify it fails**

Run:

```bash
pytest tests/test_web_mysql_store.py -v
```

Expected: import failure because `mysql_store.py` does not exist.

- [ ] **Step 4: Define ordered MySQL migrations**

`mysql_migrations.py` must expose:

```python
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INT PRIMARY KEY,
            applied_at VARCHAR(40) NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            `key` VARCHAR(100) PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at VARCHAR(40) NOT NULL,
            updated_by VARCHAR(255) NOT NULL DEFAULT ''
        );
        """,
    ),
    # Continue version 1 with the existing application tables.
)
```

The complete version-1 schema must include:

- `admin_sessions`
- `access_whitelist`
- `app_users`
- `model_configs`
- `analysis_reports`
- `analysis_runs`
- `analysis_run_events`
- `active_analysis_owners`

Use `BIGINT AUTO_INCREMENT` for numeric IDs, `VARCHAR(36)` for UUID run IDs, `LONGTEXT` for JSON/report bodies, and InnoDB tables with `utf8mb4`.

- [ ] **Step 5: Implement migration execution and MySQL connections**

`MySQLApplicationStore` must:

```python
class MySQLApplicationStore:
    def __init__(self, database_url: str, master_key: bytes):
        self.database_url = database_url
        self.master_key = master_key
        self._connection_args = parse_mysql_url(database_url)
        self._apply_migrations()
        self._seed_runtime_settings()

    def _connect(self):
        return pymysql.connect(
            **self._connection_args,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            charset="utf8mb4",
        )
```

Parse only `mysql://` and `mysql+pymysql://` URLs. Reject other schemes with `ValueError`.

Apply each migration in order, record it in `schema_migrations` in the same transaction, and never drop tables at startup.

- [ ] **Step 6: Port store behavior with MySQL transaction semantics**

Implement the existing `AdminStore` public methods with the same input and return shapes. Required transaction differences:

- `create_analysis_run()` locks the runtime-setting rows, checks paused/queue state, inserts `active_analysis_owners(owner_key, run_id)`, then inserts the queued run. Duplicate owner keys raise `ActiveRunExists` after loading the existing run.
- `claim_next_analysis_run()` selects the oldest queued task with `FOR UPDATE`, updates it to `running`, and commits.
- `append_analysis_event()` locks the run row, increments `last_event_seq`, inserts the event, and commits.
- `complete_analysis_run()` and `fail_analysis_run()` delete the matching `active_analysis_owners` row in the same transaction as the terminal status update.
- model secrets use `AESGCM(self.master_key)`; the master key is never inserted into `app_settings`.
- `reset_test_data()` deletes rows from application tables in child-to-parent order but does not drop schema or migrations.

- [ ] **Step 7: Add the runtime store factory**

```python
# tradingagents/web/store_factory.py
from __future__ import annotations

from .admin_store import AdminStore
from .mysql_store import MySQLApplicationStore
from .runtime import WebRuntimeConfig, load_web_runtime_config
from .store import ApplicationStore

_STORE: ApplicationStore | None = None


def create_application_store(config: WebRuntimeConfig) -> ApplicationStore:
    if config.mode == "local":
        return AdminStore.from_database_url(config.database_url)
    assert config.master_key is not None
    return MySQLApplicationStore(config.database_url, config.master_key)


def get_application_store() -> ApplicationStore:
    global _STORE
    if _STORE is None:
        _STORE = create_application_store(load_web_runtime_config())
    return _STORE
```

Add `AdminStore.from_database_url()` to convert the local SQLite URL into the existing path constructor.

Update `api.py` and `runner.py` imports in later tasks; in this task, retain `get_admin_store()` as a deprecated delegating wrapper so existing tests stay green.

- [ ] **Step 8: Add a MySQL CI service job**

Add a Python 3.12 job to `.github/workflows/ci.yml`:

```yaml
  mysql-store:
    name: mysql store contract
    runs-on: ubuntu-latest
    services:
      mysql:
        image: mysql:8.4
        env:
          MYSQL_ROOT_PASSWORD: rootpass
          MYSQL_DATABASE: tradingagents_test
        ports:
          - 3306:3306
        options: >-
          --health-cmd="mysqladmin ping -h 127.0.0.1 -prootpass"
          --health-interval=10s
          --health-timeout=5s
          --health-retries=10
    env:
      TRADINGAGENTS_TEST_MYSQL_URL: mysql+pymysql://root:rootpass@127.0.0.1:3306/tradingagents_test
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev,web,cloudbase]"
      - run: pytest tests/test_web_mysql_store.py -v
```

- [ ] **Step 9: Run storage tests**

Run local:

```bash
pytest tests/test_web_admin_store.py tests/test_web_store_contract.py -v
```

If local MySQL is available, also run:

```bash
pytest tests/test_web_mysql_store.py -v
```

Expected: SQLite contract passes; MySQL contract passes when configured or skips with the documented reason.

- [ ] **Step 10: Commit MySQL support**

```bash
git add pyproject.toml .github/workflows/ci.yml tradingagents/web/admin_store.py tradingagents/web/mysql_migrations.py tradingagents/web/mysql_store.py tradingagents/web/store_factory.py tradingagents/web/runner.py tests/test_web_store_contract.py tests/test_web_mysql_store.py
git commit -m "feat: add CloudBase MySQL storage"
```

---

### Task 5: Local and CloudBase Identity Providers

**Files:**
- Modify: `tradingagents/web/identity.py`
- Create: `tests/test_web_identity.py`
- Modify: `tradingagents/web/store.py`
- Modify: `tradingagents/web/admin_store.py`
- Modify: `tradingagents/web/mysql_store.py`

**Interfaces:**
- Produces:
  - `parse_cloudbase_context(value: str | None) -> dict[str, object]`
  - `LocalIdentityProvider`
  - `CloudBaseIdentityProvider`
  - `create_identity_provider(config, store)`
- `Principal` gains `role: str`; `Principal.is_admin` remains the authorization shortcut.
- Store gains:
  - `get_app_user(uid: str) -> dict[str, object] | None`
  - `list_app_users() -> list[dict[str, object]]`
  - `upsert_app_user(payload: dict[str, object]) -> dict[str, object]`
  - `delete_app_user(uid: str) -> bool`

- [ ] **Step 1: Write failing CloudBase identity tests**

```python
# tests/test_web_identity.py
import base64
import json

import pytest

from tradingagents.web.identity import (
    CloudBaseIdentityProvider,
    IdentityRequired,
    parse_cloudbase_context,
)


def _context(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


class UserStore:
    def __init__(self, user):
        self.user = user

    def get_app_user(self, uid):
        if self.user and self.user["uid"] == uid:
            return self.user
        return None


def test_cloudbase_context_decodes_uid_and_email():
    parsed = parse_cloudbase_context(
        _context({"uid": "cb-123", "email": "User@Example.com"})
    )

    assert parsed["uid"] == "cb-123"
    assert parsed["email"] == "User@Example.com"


def test_cloudbase_provider_uses_database_role_not_browser_role():
    provider = CloudBaseIdentityProvider(
        UserStore(
            {
                "uid": "cb-123",
                "email": "user@example.com",
                "role": "user",
                "status": "active",
            }
        )
    )
    principal = provider.from_headers(
        {
            "x-cloudbase-context": _context(
                {"uid": "cb-123", "email": "user@example.com", "role": "admin"}
            )
        }
    )

    assert principal.owner_key == "uid:cb-123"
    assert principal.role == "user"
    assert principal.is_admin is False


def test_cloudbase_provider_rejects_missing_unknown_and_disabled_users():
    with pytest.raises(IdentityRequired):
        CloudBaseIdentityProvider(UserStore(None)).from_headers({})

    with pytest.raises(PermissionError):
        CloudBaseIdentityProvider(UserStore(None)).from_headers(
            {"x-cloudbase-context": _context({"uid": "unknown"})}
        )

    with pytest.raises(PermissionError):
        CloudBaseIdentityProvider(
            UserStore(
                {
                    "uid": "blocked",
                    "email": "",
                    "role": "user",
                    "status": "disabled",
                }
            )
        ).from_headers(
            {"x-cloudbase-context": _context({"uid": "blocked"})}
        )
```

- [ ] **Step 2: Verify identity tests fail**

Run:

```bash
pytest tests/test_web_identity.py -v
```

Expected: import failures for new identity-provider symbols.

- [ ] **Step 3: Implement strict CloudBase context parsing**

```python
def parse_cloudbase_context(value: str | None) -> dict[str, object]:
    if not value:
        raise IdentityRequired("CloudBase identity context is required")
    try:
        decoded = base64.b64decode(value, validate=True)
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise IdentityRequired("CloudBase identity context is invalid") from exc
    if not isinstance(payload, dict) or not str(payload.get("uid") or "").strip():
        raise IdentityRequired("CloudBase identity context has no uid")
    return payload
```

Do not fall back to `x-cloudbase-uid`, `x-user-uid`, query email, or local admin cookies in `CloudBaseIdentityProvider`.

- [ ] **Step 4: Implement role lookup and local compatibility**

```python
class CloudBaseIdentityProvider:
    def __init__(self, store: ApplicationStore):
        self.store = store

    def from_headers(self, headers: Mapping[str, str]) -> Principal:
        context = parse_cloudbase_context(headers.get("x-cloudbase-context"))
        uid = str(context["uid"]).strip()
        user = self.store.get_app_user(uid)
        if user is None or user["status"] != "active":
            raise PermissionError("CloudBase user is not allowed")
        role = str(user["role"])
        return Principal(
            owner_key=f"uid:{uid}",
            uid=uid,
            email=str(user.get("email") or "").strip().lower(),
            role=role,
            is_admin=role == "admin",
        )
```

Move the existing request-value behavior into `LocalIdentityProvider`, including local admin-token verification and access-email fallback.

- [ ] **Step 5: Add `app_users` behavior to both stores**

SQLite migration creates:

```sql
CREATE TABLE IF NOT EXISTS app_users (
    uid TEXT PRIMARY KEY,
    email TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'active',
    daily_limit INTEGER NOT NULL DEFAULT 5,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Validate:

- UID is non-empty.
- role is `admin` or `user`.
- status is `active` or `disabled`.
- daily limit is a non-negative integer.
- email is lowercased.

Implement equivalent MySQL queries and include the table in version-1 migrations.

- [ ] **Step 6: Run identity and store tests**

Run:

```bash
pytest tests/test_web_identity.py tests/test_web_admin_store.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit identity providers**

```bash
git add tradingagents/web/identity.py tradingagents/web/store.py tradingagents/web/admin_store.py tradingagents/web/mysql_store.py tests/test_web_identity.py tests/test_web_admin_store.py
git commit -m "feat: add CloudBase identity provider"
```

---

### Task 6: Runtime, Session, User, and Scheduler Administration APIs

**Files:**
- Modify: `tradingagents/web/api.py`
- Modify: `tests/test_web_admin_api.py`
- Modify: `tests/test_web_stock_and_reports.py`

**Interfaces:**
- Consumes: runtime config, store factory, identity provider, and scheduler.
- Produces endpoints:
  - `GET /api/runtime-config`
  - `GET /api/session`
  - `GET /api/admin/users`
  - `POST /api/admin/users`
  - `DELETE /api/admin/users/{uid}`
  - `GET /api/admin/runtime-settings`
  - `PUT /api/admin/runtime-settings`
- Existing local admin setup/login endpoints remain functional only in local mode.

- [ ] **Step 1: Write failing API tests for both modes**

```python
# append to tests/test_web_admin_api.py
import base64
import json

from tradingagents.web.runtime import WebRuntimeConfig


def _cloud_context(uid: str) -> str:
    payload = json.dumps({"uid": uid}).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def test_runtime_config_exposes_only_public_cloudbase_values(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    runtime = WebRuntimeConfig(
        mode="cloudbase",
        database_url="mysql+pymysql://secret",
        cloudbase_env_id="env-123",
        cloudbase_region="ap-shanghai",
        cloudbase_publishable_key="publishable",
        master_key=b"k" * 32,
    )
    client = TestClient(api.create_app(store=store, runtime=runtime))

    response = client.get("/api/runtime-config")

    assert response.json() == {
        "runtime": "cloudbase",
        "auth": "cloudbase",
        "env_id": "env-123",
        "region": "ap-shanghai",
        "publishable_key": "publishable",
        "sdk_url": "https://static.cloudbase.net/cloudbase-js-sdk/2.28.6/cloudbase.full.js",
    }
    assert "mysql" not in response.text
    assert "master" not in response.text


def test_cloudbase_admin_can_update_runtime_settings(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    store.upsert_app_user(
        {"uid": "admin-1", "role": "admin", "status": "active"}
    )
    runtime = WebRuntimeConfig(
        mode="cloudbase",
        database_url="mysql+pymysql://unused",
        cloudbase_env_id="env-123",
        cloudbase_publishable_key="publishable",
        master_key=b"k" * 32,
    )
    service = FakeTaskService()
    client = TestClient(
        api.create_app(store=store, runtime=runtime, task_service=service)
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
    runtime = WebRuntimeConfig(
        mode="cloudbase",
        database_url="mysql+pymysql://unused",
        cloudbase_env_id="env-123",
        cloudbase_publishable_key="publishable",
        master_key=b"k" * 32,
    )
    client = TestClient(api.create_app(store=store, runtime=runtime))

    response = client.get(
        "/api/admin/runtime-settings",
        headers={"x-cloudbase-context": _cloud_context("user-1")},
    )

    assert response.status_code == 403
```

Extend `FakeTaskService` with:

```python
def __init__(self):
    self.notified = False

def start(self):
    return None

def notify(self):
    self.notified = True
```

- [ ] **Step 2: Run API tests and verify they fail**

Run:

```bash
pytest tests/test_web_admin_api.py -v
```

Expected: `create_app` does not accept injected dependencies and endpoints are absent.

- [ ] **Step 3: Inject runtime, store, identity, and task service**

Change the app factory signature:

```python
def create_app(
    *,
    store: ApplicationStore | None = None,
    runtime: WebRuntimeConfig | None = None,
    task_service: AnalysisTaskService | None = None,
):
    runtime = runtime or load_web_runtime_config()
    store = store or get_application_store()
    identity_provider = create_identity_provider(runtime, store)
    task_service = task_service or create_task_service(store)
```

Startup calls `task_service.start()`. Shutdown retains `shutdown(wait=True)`.

Replace `_principal_from_request()` internals with the selected identity provider. Map:

- `IdentityRequired` to `401`;
- `PermissionError` to `403`.

- [ ] **Step 4: Add public runtime and protected session endpoints**

Return the exact public response asserted in the test. `GET /api/session` returns:

```json
{
  "user": {
    "uid": "cb-uid",
    "email": "user@example.com",
    "role": "admin",
    "is_admin": true
  }
}
```

Do not include tokens, database values, or model keys.

- [ ] **Step 5: Add administrator users and runtime-settings APIs**

Use `require_admin(principal)` based on the resolved principal rather than directly verifying a local cookie.

`PUT /api/admin/runtime-settings` calls:

```python
settings = store.update_runtime_settings(
    payload,
    updated_by=principal.owner_key,
)
task_service.notify()
return {"settings": asdict(settings)}
```

User endpoints call the exact store methods from Task 5. Prevent deleting the currently authenticated administrator UID with `409`.

- [ ] **Step 6: Map task admission errors**

In `POST /api/runs`:

```python
except QueueLimitReached as exc:
    raise HTTPException(
        status_code=429,
        detail={"error_type": "QueueLimitReached", "message": str(exc)},
    ) from exc
except TaskSubmissionPaused as exc:
    raise HTTPException(
        status_code=503,
        detail={"error_type": "TaskSubmissionPaused", "message": str(exc)},
    ) from exc
```

After successful creation call `task_service.notify()` rather than directly submitting the run.

- [ ] **Step 7: Restrict local administrator-password endpoints**

In CloudBase mode, `/api/admin/setup` and `/api/admin/login` return `404`. Local mode retains current behavior and tests.

- [ ] **Step 8: Run Web API regressions**

Run:

```bash
pytest tests/test_web_admin_api.py tests/test_web_stock_and_reports.py tests/test_web_task_service.py -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit the API integration**

```bash
git add tradingagents/web/api.py tests/test_web_admin_api.py tests/test_web_stock_and_reports.py
git commit -m "feat: expose CloudBase admin and runtime APIs"
```

---

### Task 7: CloudBase Web Login and Role-Aware Workbench

**Files:**
- Modify: `tradingagents/web/static/index.html`
- Modify: `tradingagents/web/static/workbench.js`
- Modify: `tradingagents/web/static/workbench.css`
- Modify: `tests/test_web_workbench_static.py`
- Modify: `tests/test_web_static.py`

**Interfaces:**
- Consumes: `/api/runtime-config`, `/api/session`, and CloudBase SDK v2.
- Produces browser helpers:
  - `loadRuntimeConfig()`
  - `loadCloudBaseSdk(url)`
  - `restoreCloudBaseSession()`
  - `signInCloudBase(username, password)`
  - `signOutCloudBase()`
  - `authHeaders()`
- Local administrator login behavior remains unchanged.

- [ ] **Step 1: Write failing static-contract tests**

```python
# append to tests/test_web_workbench_static.py
def test_workbench_supports_cloudbase_runtime_and_access_tokens():
    assert "function loadRuntimeConfig()" in JS
    assert "function loadCloudBaseSdk(url)" in JS
    assert "function restoreCloudBaseSession()" in JS
    assert "function signInCloudBase(username, password)" in JS
    assert "Authorization: `Bearer ${state.accessToken}`" in JS
    assert "/api/runtime-config" in JS
    assert "/api/session" in JS


def test_cloudbase_login_ui_exists_without_removing_local_admin_login():
    assert 'id="cloudbase-auth-modal"' in HTML
    assert 'id="cloudbase-username"' in HTML
    assert 'id="cloudbase-password"' in HTML
    assert 'id="admin-modal"' in HTML
```

- [ ] **Step 2: Run static tests and verify they fail**

Run:

```bash
pytest tests/test_web_workbench_static.py tests/test_web_static.py -v
```

Expected: missing CloudBase functions and modal.

- [ ] **Step 3: Add runtime and session state**

Extend the top-level state:

```javascript
runtime: { runtime: 'local', auth: 'local' },
cloudbaseApp: null,
cloudbaseAuth: null,
accessToken: '',
currentUser: null,
```

`loadRuntimeConfig()` calls `/api/runtime-config` before protected API initialization.

- [ ] **Step 4: Load the pinned SDK only in CloudBase mode**

```javascript
function loadCloudBaseSdk(url) {
  return new Promise((resolve, reject) => {
    if (window.cloudbase) return resolve(window.cloudbase);
    const script = document.createElement('script');
    script.src = url;
    script.async = true;
    script.onload = () => resolve(window.cloudbase);
    script.onerror = () => reject(new Error('CloudBase 登录组件加载失败'));
    document.head.appendChild(script);
  });
}
```

Initialize:

```javascript
state.cloudbaseApp = window.cloudbase.init({
  env: state.runtime.env_id,
  region: state.runtime.region,
  accessKey: state.runtime.publishable_key,
});
state.cloudbaseAuth = state.cloudbaseApp.auth();
```

- [ ] **Step 5: Implement login, restore, and logout**

Use the v2 SDK result shapes:

```javascript
async function signInCloudBase(username, password) {
  const result = await state.cloudbaseAuth.signInWithPassword({
    username,
    password,
  });
  if (result.error) throw result.error;
  await restoreCloudBaseSession();
}

async function restoreCloudBaseSession() {
  const result = await state.cloudbaseAuth.getSession();
  const session = result.data?.session;
  state.accessToken = session?.access_token || '';
  if (!state.accessToken) {
    state.currentUser = null;
    return false;
  }
  const response = await apiJson('/api/session', {
    headers: authHeaders(),
  });
  state.currentUser = response.user;
  return true;
}

async function signOutCloudBase() {
  await state.cloudbaseAuth.signOut();
  state.accessToken = '';
  state.currentUser = null;
}
```

- [ ] **Step 6: Centralize request authentication**

```javascript
function authHeaders() {
  const headers = {};
  if (state.runtime.auth === 'cloudbase' && state.accessToken) {
    headers.Authorization = `Bearer ${state.accessToken}`;
  } else if (state.adminToken) {
    headers.Authorization = `Bearer ${state.adminToken}`;
  }
  return headers;
}
```

Replace protected uses of `adminHeaders()` with `authHeaders()`. Keep a local-only helper if the administrator login setup code still needs it.

Hide administrator navigation unless:

```javascript
state.runtime.auth === 'local'
  ? Boolean(state.adminToken)
  : Boolean(state.currentUser?.is_admin)
```

- [ ] **Step 7: Add the CloudBase login modal and styles**

Add username, password, submit, status, and logout controls. Do not render model-provider API Key values into the DOM after saving; retain the existing masked display.

- [ ] **Step 8: Run static and API tests**

Run:

```bash
pytest tests/test_web_workbench_static.py tests/test_web_static.py tests/test_web_admin_api.py -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit CloudBase browser login**

```bash
git add tradingagents/web/static/index.html tradingagents/web/static/workbench.js tradingagents/web/static/workbench.css tests/test_web_workbench_static.py tests/test_web_static.py
git commit -m "feat: add CloudBase web authentication"
```

---

### Task 8: Administrator User and Dynamic Runtime Settings UI

**Files:**
- Modify: `tradingagents/web/static/workbench.js`
- Modify: `tradingagents/web/static/workbench.css`
- Modify: `tests/test_web_workbench_static.py`

**Interfaces:**
- Consumes: `/api/admin/users` and `/api/admin/runtime-settings`.
- Adds administrator panes `users` and `runtime`.
- In local mode, the existing `whitelist` pane remains visible; in CloudBase mode, `users` replaces it.

- [ ] **Step 1: Write failing UI contract tests**

```python
# append to tests/test_web_workbench_static.py
def test_admin_runtime_settings_controls_are_present():
    assert 'data-admin-pane="runtime"' in JS
    assert 'id="runtime-concurrency"' in JS
    assert 'id="runtime-queue-limit"' in JS
    assert 'id="runtime-accepting"' in JS
    assert "/api/admin/runtime-settings" in JS


def test_cloudbase_user_management_includes_role_and_status():
    assert 'data-admin-pane="users"' in JS
    assert 'id="user-role"' in JS
    assert 'id="user-status"' in JS
    assert "/api/admin/users" in JS
```

- [ ] **Step 2: Verify UI tests fail**

Run:

```bash
pytest tests/test_web_workbench_static.py -v
```

Expected: missing runtime and users panes.

- [ ] **Step 3: Add administrator state and data loading**

```javascript
adminUsers: [],
runtimeSettings: null,
runtimeSettingsWarning: '',
```

In `loadAdminData()`:

- always load model configs and runtime settings;
- load whitelist in local mode;
- load app users in CloudBase mode.

- [ ] **Step 4: Render validated runtime controls**

Render numeric inputs:

```html
<input id="runtime-concurrency" type="number" min="1" max="8">
<input id="runtime-queue-limit" type="number" min="1" max="200">
<input id="runtime-accepting" type="checkbox">
```

Display the store warning in a visible warning status box. Save with:

```javascript
await apiJson('/api/admin/runtime-settings', {
  method: 'PUT',
  headers: authHeaders(),
  body: JSON.stringify({
    analysis_concurrency_limit: Number(qs('#runtime-concurrency').value),
    analysis_queue_limit: Number(qs('#runtime-queue-limit').value),
    accept_new_tasks: qs('#runtime-accepting').checked,
  }),
});
```

- [ ] **Step 5: Render CloudBase user role management**

The user editor submits:

```javascript
{
  uid: qs('#user-uid').value.trim(),
  email: qs('#user-email').value.trim(),
  display_name: qs('#user-display-name').value.trim(),
  role: qs('#user-role').value,
  status: qs('#user-status').value,
  daily_limit: Number(qs('#user-daily-limit').value),
}
```

Do not permit editing the UID of an existing selected user. Show `admin` and `user` roles and `active` and `disabled` states.

- [ ] **Step 6: Preserve local administrator behavior**

Local mode still shows:

- model management;
- whitelist management;
- runtime settings;
- local administrator logout.

CloudBase mode shows:

- model management;
- user management;
- runtime settings;
- CloudBase logout.

- [ ] **Step 7: Run frontend regressions**

Run:

```bash
pytest tests/test_web_workbench_static.py tests/test_web_static.py -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit administrator controls**

```bash
git add tradingagents/web/static/workbench.js tradingagents/web/static/workbench.css tests/test_web_workbench_static.py
git commit -m "feat: add runtime and user administration"
```

---

### Task 9: CloudBase Docker Image, Git Deployment Guide, and Final Verification

**Files:**
- Create: `.dockerignore`
- Create: `docs/deployment/cloudbase.md`
- Modify: `Dockerfile`
- Modify: `README.md`
- Modify: `tests/test_web_static.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Docker image starts `tradingagents-web --host 0.0.0.0`.
- CloudBase injects `PORT`.
- `/healthz` is liveness; add `/readyz` for database readiness.
- GitHub deployment documentation contains exact CloudBase settings and first-admin SQL.

- [ ] **Step 1: Add failing container configuration tests**

```python
# append to tests/test_web_static.py
def test_dockerfile_starts_web_app_and_installs_cloudbase_extras():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert 'pip install --no-cache-dir ".[web,china,cloudbase]"' in dockerfile
    assert 'CMD ["tradingagents-web", "--host", "0.0.0.0"]' in dockerfile
    assert 'ENTRYPOINT ["tradingagents"]' not in dockerfile


def test_dockerignore_excludes_secrets_and_local_state():
    ignored = Path(".dockerignore").read_text(encoding="utf-8")
    for value in (".env", ".git", ".tradingagents", "__pycache__", ".pytest_cache"):
        assert value in ignored
```

- [ ] **Step 2: Run the container configuration tests**

Run:

```bash
pytest tests/test_web_static.py -v
```

Expected: failure because the Dockerfile is still CLI-oriented and `.dockerignore` is absent.

- [ ] **Step 3: Update the Docker image**

Use:

```dockerfile
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY . .
RUN pip install --no-cache-dir ".[web,china,cloudbase]"

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home appuser \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents

USER appuser
WORKDIR /home/appuser/app
COPY --from=builder --chown=appuser:appuser /build .

CMD ["tradingagents-web", "--host", "0.0.0.0"]
```

- [ ] **Step 4: Add `.dockerignore`**

Include:

```text
.git
.github
.env
.env.*
.tradingagents
__pycache__
*.pyc
.pytest_cache
.ruff_cache
.venv
venv
reports
results
worklog
.playwright-mcp
```

Do not ignore source tests before the Docker build unless CI builds from a separately verified commit.

- [ ] **Step 5: Add readiness behavior**

Add `store.ping() -> bool` to both stores:

- SQLite executes `SELECT 1`.
- MySQL borrows a pooled/direct connection and executes `SELECT 1`.

Expose:

```python
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    if not store.ping():
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"ok": True}
```

Add focused API tests for `200` and mocked `503`.

- [ ] **Step 6: Write the exact CloudBase deployment guide**

`docs/deployment/cloudbase.md` must document:

1. Create a Shanghai CloudBase environment and initialize CloudBase MySQL.
2. Enable the CloudBase Run service VPC connection to the database VPC.
3. Bind the GitHub repository and production branch.
4. Set build file `Dockerfile`, container port to the CloudBase `PORT`, min instances `1`, max instances `1`, and Uvicorn workers `1`.
5. Configure:

```text
TRADINGAGENTS_RUNTIME=cloudbase
TRADINGAGENTS_DATABASE_URL=mysql+pymysql://USER:PASSWORD@HOST:3306/tcb
TRADINGAGENTS_CLOUDBASE_ENV_ID=...
TRADINGAGENTS_CLOUDBASE_REGION=ap-shanghai
TRADINGAGENTS_CLOUDBASE_PUBLISHABLE_KEY=...
TRADINGAGENTS_MASTER_KEY=<URL-safe base64 for exactly 32 bytes>
```

6. State explicitly that model-provider API keys are entered later in the Web administrator UI.
7. Configure public unauthenticated access for `/`, `/assets/*`, `/api/runtime-config`, `/healthz`, and `/readyz`; enable HTTP identity authentication for protected `/api/*` paths.
8. Add the deployed domain to CloudBase Web secure sources.
9. Log in once, get the UID from CloudBase user management, and bootstrap:

```sql
INSERT INTO app_users (
    uid, email, display_name, role, status, daily_limit, created_at, updated_at
) VALUES (
    'CLOUDBASE_UID',
    'admin@example.com',
    'Administrator',
    'admin',
    'active',
    100,
    UTC_TIMESTAMP(6),
    UTC_TIMESTAMP(6)
)
ON DUPLICATE KEY UPDATE
    role = 'admin',
    status = 'active',
    updated_at = UTC_TIMESTAMP(6);
```

10. Push a commit and verify automatic build, `/healthz`, `/readyz`, login, model-key save, two running tasks, and one queued task.

- [ ] **Step 7: Update README deployment links**

Add a short “CloudBase deployment” section linking to the guide and preserving the existing local installation instructions.

- [ ] **Step 8: Add CI Docker build smoke**

Add a job:

```yaml
  docker-build:
    name: docker build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t tradingagents-cloudbase .
```

- [ ] **Step 9: Run the consolidated verification pass**

Run:

```bash
pytest tests/test_web_runtime.py tests/test_web_admin_store.py tests/test_web_identity.py tests/test_web_task_service.py tests/test_web_admin_api.py tests/test_web_stock_and_reports.py tests/test_web_workbench_static.py tests/test_web_static.py -v
ruff check tradingagents/web tests/test_web_runtime.py tests/test_web_admin_store.py tests/test_web_identity.py tests/test_web_task_service.py tests/test_web_admin_api.py tests/test_web_stock_and_reports.py tests/test_web_workbench_static.py tests/test_web_static.py
docker build -t tradingagents-cloudbase .
```

Expected:

- all selected tests pass;
- Ruff reports no errors;
- Docker image builds successfully and ends with the Web `CMD`.

If `TRADINGAGENTS_TEST_MYSQL_URL` is configured, also run:

```bash
pytest tests/test_web_mysql_store.py -v
```

Expected: MySQL contract passes.

- [ ] **Step 10: Commit deployment support**

```bash
git add Dockerfile .dockerignore README.md docs/deployment/cloudbase.md tradingagents/web/api.py tradingagents/web/admin_store.py tradingagents/web/mysql_store.py tests/test_web_static.py tests/test_web_admin_api.py .github/workflows/ci.yml
git commit -m "feat: add CloudBase deployment workflow"
```

---

## Integration Checkpoint

After all nine tasks:

1. Start locally with no CloudBase variables:

```bash
tradingagents-web --host 127.0.0.1 --port 8000
```

Expected: existing local administrator setup, SQLite storage, model configuration, task execution, report history, and dynamic runtime settings work.

2. Start against a test MySQL database with CloudBase runtime configuration and a valid 32-byte master key.

Expected: startup applies only missing migrations, rejects requests without CloudBase context, authorizes seeded users by database role, and never writes the master key into MySQL.

3. Confirm Git status contains only intentional implementation files.

4. Run the repository-wide integration pass before push:

```bash
pytest -q
ruff check .
docker build -t tradingagents-cloudbase .
```

5. Review logs from model catalog and analysis failures and confirm no full model-provider API Key, database password, Authorization token, or master key appears.

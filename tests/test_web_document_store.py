import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from tests.test_web_store_contract import assert_store_contract
from tradingagents.web.admin_store import ActiveRunExists
from tradingagents.web.document_client import DocumentClient, DocumentDatabaseError, decode_ejson
from tradingagents.web.document_store import CloudBaseDocumentStore
from tradingagents.web.events import AnalysisEvent
from tradingagents.web.runtime import load_web_runtime_config
from tradingagents.web.store import QueueLimitReached, TaskSubmissionPaused
from tradingagents.web.task_service import AnalysisTaskService


class MemoryDocumentClient:
    """Transactional test double; transport contracts are tested separately."""

    def __init__(self):
        self.data = {}
        self.lock = threading.RLock()

    def atomic(self, operation):
        with self.lock:
            original = copy.deepcopy(self.data)
            try:
                return operation("test-tx")
            except Exception:
                self.data = original
                raise

    def get(self, collection, doc_id, tx=None):
        return copy.deepcopy(self.data.get((collection, str(doc_id))))

    def put(self, collection, doc_id, data, tx=None):
        self.data[(collection, str(doc_id))] = copy.deepcopy(data)

    def delete(self, collection, doc_id, tx=None):
        return self.data.pop((collection, str(doc_id)), None) is not None

    def query(self, collection, query=None, *, order=None, limit=None, projection=None, tx=None):
        def matches(row, query):
            for key, value in query.items():
                if key == "$or":
                    if not any(matches(row, item) for item in value):
                        return False
                elif isinstance(value, dict) and "$gt" in value:
                    if row.get(key, 0) <= value["$gt"]:
                        return False
                elif row.get(key) != value:
                    return False
            return True

        rows = [copy.deepcopy(row) for (name, _), row in self.data.items()
                if name == collection and matches(row, query or {})]
        for field, direction in reversed(order or []):
            rows.sort(key=lambda row, field=field: row[field], reverse=direction == "desc")
        if limit is not None:
            rows = rows[:limit]
        for row in rows:
            for key, include in (projection or {}).items():
                if not include:
                    row.pop(key, None)
        return rows


@pytest.fixture
def store():
    return CloudBaseDocumentStore("env-test", "server-key", b"k" * 32,
                                  client=MemoryDocumentClient())


def run_payload(owner="uid:u1"):
    return {"owner_key": owner, "owner_uid": owner, "ticker": "600519.SH",
            "trade_date": "2026-10-21", "analysts": ["market"]}


def test_document_store_matches_existing_contract(store):
    assert_store_contract(store)


def test_queue_atomicity_owner_isolation_and_recovery(store):
    run = store.create_analysis_run(run_payload())
    with pytest.raises(ActiveRunExists):
        store.create_analysis_run(run_payload())
    assert store.count_queued_analysis_runs() == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.claim_next_analysis_run(), range(4)))
    assert sum(item is not None for item in results) == 1
    assert store.count_running_analysis_runs() == 1
    assert store.mark_interrupted_runs_failed() == 1
    assert store.count_running_analysis_runs() == 0
    assert store.get_active_analysis_run("uid:u1") is None
    assert store.get_analysis_run(run["id"])["error_type"] == "WorkerInterrupted"
    store.create_analysis_run(run_payload())


def test_settings_changes_apply_to_admission_without_cached_bypass(store):
    payload = {"analysis_concurrency_limit": 3, "analysis_queue_limit": 1, "accept_new_tasks": True}
    store.update_runtime_settings(payload, "admin")
    store.create_analysis_run(run_payload())
    with pytest.raises(QueueLimitReached):
        store.create_analysis_run(run_payload("uid:u2"))
    store.update_runtime_settings({**payload, "accept_new_tasks": False}, "admin")
    with pytest.raises(TaskSubmissionPaused):
        store.create_analysis_run(run_payload("uid:u3"))
    assert store.get_runtime_settings().analysis_concurrency_limit == 3


def test_events_are_monotonic_and_resume_from_sequence(store):
    run = store.create_analysis_run(run_payload())
    with ThreadPoolExecutor(max_workers=4) as pool:
        events = list(pool.map(lambda n: store.append_analysis_event(
            run["id"], AnalysisEvent("progress", {"n": n})), range(12)))
    assert sorted(event["seq"] for event in events) == list(range(1, 13))
    assert [event["seq"] for event in store.list_analysis_events(run["id"], 9)] == [10, 11, 12]


def test_document_task_completion_never_serializes_graph_objects(store, monkeypatch, caplog):
    original_put = store.db.put

    def strict_json_put(collection, doc_id, data, tx=None):
        # Match requests' real JSON boundary (the memory store alone accepts
        # arbitrary Python objects and previously hid this production failure).
        json.dumps(data, allow_nan=False)
        return original_put(collection, doc_id, data, tx)

    monkeypatch.setattr(store.db, "put", strict_json_put)
    run = store.create_analysis_run(run_payload())
    final_state = {
        "messages": [AIMessage(content="internal answer"),
                     ToolMessage(content="raw tool data", tool_call_id="call-1")],
        "runtime_object": object(), "large_trace": "trace " * 200_000,
    }
    events = [
        AnalysisEvent("report_section_updated", {"section": "market_report", "content": "分析报告"}),
        AnalysisEvent("run_completed", {"final_state": final_state}),
    ]
    service = AnalysisTaskService(store, graph_builder=lambda request: object(),
                                  event_stream=lambda graph, request: events)
    try:
        service.submit(run["id"]).result(timeout=2)
    finally:
        service.shutdown()
    saved = store.get_analysis_run(run["id"])
    assert saved["status"] == "completed"
    assert store.get_analysis_report(saved["report_id"])["sections"] == {"market_report": "分析报告"}
    completed = store.list_analysis_events(run["id"])[-1]
    assert completed["event"] == "run_completed"
    assert completed["data"] == {"run_id": run["id"], "report_id": saved["report_id"]}
    assert saved["last_event_seq"] == completed["seq"] == 2
    assert store.get_active_analysis_run(run["owner_key"]) is None
    assert store.count_running_analysis_runs() == 0
    assert not caplog.records


def test_reports_are_private_and_metadata_does_not_download_sections(store):
    report = store.save_analysis_report({**run_payload(), "sections": {"market_report": "内容"}})
    assert store.get_analysis_report(report["id"], "uid:u2") is None
    assert store.delete_analysis_report(report["id"], "uid:u2") is False
    metadata = store.list_analysis_reports(owner_key="uid:u1")[0]
    assert "sections" not in metadata
    assert metadata["section_keys"] == ["market_report"]
    assert store.get_analysis_report(report["id"], "uid:u1")["sections"]
    assert store.delete_analysis_report(report["id"], "uid:u1")


def test_users_models_and_secret_preservation(store):
    user = store.upsert_app_user({"uid": "u1", "role": "admin"})
    assert user["role"] == "admin"
    assert store.get_app_user("u1") == user
    models = []
    for number in range(2):
        models.append(store.save_model_config({"provider": "openai", "quick_model": "q",
            "deep_model": "d", "api_key": f"sk-test-{number}", "is_default": True}))
    assert [model["is_default"] for model in store.list_model_configs()] == [True, False]
    store.set_default_model_config(models[0]["id"])
    assert store.get_default_runtime_model().api_key == "sk-test-0"
    store.save_model_config({**models[0], "display_name": "updated", "api_key": ""})
    assert store.get_runtime_model_config(models[0]["id"]).api_key == "sk-test-0"
    assert "sk-test-0" not in str(store.db.data)


def test_password_sessions_and_whitelist(store):
    store.set_admin_password("long-enough-password")
    assert store.verify_admin_password("long-enough-password")
    assert not store.verify_admin_password("wrong")
    assert not store.is_identity_allowed(email="user@example.com")
    entry = store.upsert_whitelist({"email": "User@Example.com"})
    assert store.is_identity_allowed(email="user@example.com")
    token = store.create_admin_session()
    assert store.verify_admin_session(token)
    assert not store.verify_admin_session("bad")
    store.delete_whitelist(entry["id"])
    assert not store.is_identity_allowed(email="user@example.com")


def test_document_runtime_requires_server_key_but_not_mysql_url():
    import base64

    config = load_web_runtime_config({"TRADINGAGENTS_RUNTIME": "cloudbase",
        "TRADINGAGENTS_DATABASE_BACKEND": "document", "TRADINGAGENTS_CLOUDBASE_ENV_ID": "env-test",
        "TRADINGAGENTS_CLOUDBASE_API_KEY": "private-key",
        "TRADINGAGENTS_MASTER_KEY": base64.urlsafe_b64encode(b"k" * 32).decode()})
    assert config.database_url == ""
    assert config.database_backend == "document"
    assert "private-key" not in repr(config)
    assert load_web_runtime_config({}).mode == "local"


def test_ejson_integer_decoding():
    assert decode_ejson({"x": [{"$numberLong": "123"}, {"$numberInt": "2"}]}) == {"x": [123, 2]}


def test_ambiguous_commit_uses_receipt_without_replaying(monkeypatch):
    import requests

    client = DocumentClient("env-test", "private-key")
    calls = []

    def request(method, path, **kwargs):
        calls.append(path)
        if path == "/transactions":
            return {"transactionId": "tx"}
        if path.endswith("/commit"):
            raise requests.Timeout("lost response")
        if path.startswith("/collections/ta_operations"):
            return {"result": 42}
        return {"list": [[{"ok": 1}]]}

    monkeypatch.setattr(client, "request", request)
    assert client.atomic(lambda tx: 42) == 42
    assert calls.count("/transactions") == 1


def test_conflict_retries_and_business_failure_rolls_back(monkeypatch):
    client = DocumentClient("env-test", "private-key")
    attempts = []

    def request(method, path, **kwargs):
        if path == "/transactions":
            return {"transactionId": "tx"}
        return {"list": [[{"ok": 1}]]}

    monkeypatch.setattr(client, "request", request)

    def operation(tx):
        attempts.append(tx)
        if len(attempts) == 1:
            raise DocumentDatabaseError(503, "DATABASE_TRANSACTION_CONFLICT")
        return "ok"

    assert client.atomic(operation) == "ok"
    assert len(attempts) == 2


def test_http_error_logs_redacted_diagnostics_without_changing_request(caplog):
    client = DocumentClient("env-test", "server-secret-key")
    body = {"transactionId": "private-tx", "commands": [{
        "update": "ta_events", "updates": [{"q": {"_id": "private-doc"}, "u": {
            "data": {"content": "机密报告内容", "password": "12345678"},
        }}],
    }]}
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(ok=False, status_code=400, headers={}, json=lambda: {
            "code": "INVALID_PARAM", "request_id": "51a96b26-493b-4649-9aed-f5115be80a22",
            "message": "Invalid parameter commands: BSON size exceeds 1048576 bytes. "
                       "server-secret-key private-tx private-doc 机密报告内容 12345678 "
                       "user@example.com 'do not log this'\nforged-log-line",
        })

    client._local.session = SimpleNamespace(request=request)
    with pytest.raises(DocumentDatabaseError) as error:
        client.request("POST", "/commands", body=body)
    assert error.value.code == "INVALID_PARAM"
    assert "机密" not in str(error.value)
    assert len(calls) == 1
    assert calls[0][1]["json"] == body
    assert calls[0][1]["headers"]["Authorization"] == "Bearer server-secret-key"
    log = caplog.text
    for private in ("server-secret-key", "private-tx", "private-doc", "机密报告内容",
                    "12345678", "user@example.com", "do not log this", "forged-log-line"):
        assert private not in log
    record = next(r for r in caplog.records if "diagnostic=" in r.message)
    diagnostic = json.loads(record.message.split("diagnostic=", 1)[1].split(" stack=", 1)[0])
    assert diagnostic["endpoint"] == "/commands"
    assert diagnostic["request_bytes"] == len(json.dumps(body).encode("utf-8"))
    assert diagnostic["batches"] == [{"operation": "update", "collection": "ta_events", "count": 1}]
    assert diagnostic["in_transaction"] is True
    assert "BSON size exceeds 1048576 bytes" in diagnostic["message"]
    assert "test_http_error_logs_redacted_diagnostics" in record.message


@pytest.mark.parametrize("payload", [[], None, "invalid json", {"message": {"secret": "hidden"}}])
def test_nonstandard_http_error_response_is_safe(payload, caplog):
    client = DocumentClient("env-test", "private-key")
    client._local.session = SimpleNamespace(request=lambda *a, **kw: SimpleNamespace(
        ok=False, status_code=502, headers={}, json=lambda: payload,
    ))
    with pytest.raises(DocumentDatabaseError):
        client.request("POST", "/transactions/hidden-token/commit")
    assert "/transactions/{id}/commit" in caplog.text
    assert "hidden-token" not in caplog.text
    assert "hidden" not in caplog.text


def test_expected_missing_document_does_not_emit_error_log(caplog):
    client = DocumentClient("env-test", "private-key")
    client._local.session = SimpleNamespace(request=lambda *a, **kw: SimpleNamespace(
        ok=False, status_code=404, headers={}, json=lambda: {"code": "DOCUMENT_NOT_FOUND"},
    ))
    assert client.get("ta_runs", "private-id") is None
    assert not caplog.records


def test_diagnostic_failure_preserves_original_database_error(monkeypatch):
    client = DocumentClient("env-test", "private-key")
    client._local.session = SimpleNamespace(request=lambda *a, **kw: SimpleNamespace(
        ok=False, status_code=400, headers={}, json=lambda: {"code": "INVALID_PARAM"},
    ))
    monkeypatch.setattr(client, "_log_error", lambda *args: 1 / 0)
    with pytest.raises(DocumentDatabaseError) as error:
        client.request("POST", "/commands")
    assert error.value.code == "INVALID_PARAM"

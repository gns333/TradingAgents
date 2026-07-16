from pathlib import Path
import threading
import time

from tradingagents.web.admin_store import AdminStore
from tradingagents.web.events import AnalysisEvent
from tradingagents.web.task_service import AnalysisTaskService


def _run_payload(owner_key: str, stock_name: str = "贵州茅台") -> dict:
    return {
        "owner_key": owner_key,
        "owner_uid": owner_key.removeprefix("uid:"),
        "owner_email": "user@example.com",
        "ticker": "600519.SH",
        "stock_name": stock_name,
        "trade_date": "2026-07-14",
        "asset_type": "stock",
        "analysts": ["market"],
    }


def test_service_persists_events_and_report_without_sse_client(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    operation_order: list[str] = []
    original_append_event = store.append_analysis_event
    original_save_report = store.save_analysis_report
    original_complete_run = store.complete_analysis_run

    def record_event(run_id, event):
        operation_order.append(f"event:{event.event}")
        return original_append_event(run_id, event)

    def record_report(payload):
        operation_order.append("report:saved")
        return original_save_report(payload)

    def record_completion(run_id, report_id=None):
        operation_order.append("run:completed")
        return original_complete_run(run_id, report_id)

    store.append_analysis_event = record_event
    store.save_analysis_report = record_report
    store.complete_analysis_run = record_completion
    run = store.create_analysis_run(_run_payload("uid:u1"))
    events = [
        AnalysisEvent("run_started", {}),
        AnalysisEvent(
            "report_section_updated",
            {"section": "market_report", "content": "# 市场\n\n看多"},
        ),
        AnalysisEvent("run_completed", {"final_state": {}}),
    ]
    service = AnalysisTaskService(
        store,
        graph_builder=lambda request: object(),
        event_stream=lambda graph, request: events,
    )

    service.submit(run["id"]).result(timeout=2)

    saved = store.get_analysis_run(run["id"])
    assert saved["status"] == "completed"
    assert saved["report_id"] is not None
    report = store.get_analysis_report(saved["report_id"])
    assert report["stock_name"] == "贵州茅台"
    assert report["owner_key"] == "uid:u1"
    assert report["sections"]["market_report"].startswith("# 市场")
    assert operation_order.index("report:saved") < operation_order.index(
        "event:run_completed"
    )
    assert operation_order.index("run:completed") < operation_order.index(
        "event:run_completed"
    )
    service.shutdown()


def test_service_marks_failed_event_as_terminal(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    run = store.create_analysis_run(_run_payload("uid:u1"))
    events = [
        AnalysisEvent("run_started", {}),
        AnalysisEvent(
            "run_failed",
            {"error_type": "ConnectionError", "message": "vendor unavailable"},
        ),
    ]
    service = AnalysisTaskService(
        store,
        graph_builder=lambda request: object(),
        event_stream=lambda graph, request: events,
    )

    service.submit(run["id"]).result(timeout=2)

    saved = store.get_analysis_run(run["id"])
    assert saved["status"] == "failed"
    assert saved["error_type"] == "ConnectionError"
    assert saved["report_id"] is None
    service.shutdown()


def test_recover_fails_running_run_and_executes_queued_run(tmp_path: Path):
    store = AdminStore(tmp_path / "admin.sqlite3")
    running = store.create_analysis_run(_run_payload("uid:u1"))
    store.claim_analysis_run(running["id"])
    queued = store.create_analysis_run(_run_payload("uid:u2", stock_name="比亚迪"))
    events = [AnalysisEvent("run_started", {}), AnalysisEvent("run_completed", {})]
    service = AnalysisTaskService(
        store,
        graph_builder=lambda request: object(),
        event_stream=lambda graph, request: events,
    )

    futures = service.recover()
    for future in futures:
        future.result(timeout=2)

    assert store.get_analysis_run(running["id"])["error_type"] == "WorkerInterrupted"
    assert store.get_analysis_run(queued["id"])["status"] == "completed"
    service.shutdown()


def _wait_for_count(store, status: str, expected: int, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    count = {
        "queued": store.count_queued_analysis_runs,
        "running": store.count_running_analysis_runs,
    }[status]
    while count() != expected and time.time() < deadline:
        time.sleep(0.01)
    assert count() == expected


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

    _wait_for_count(store, "running", 1)
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
    _wait_for_count(store, "running", 2)
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
    _wait_for_count(store, "running", 2)

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
    deadline = time.time() + 2
    while (
        any(
            store.get_analysis_run(run["id"])["status"] != "completed"
            for run in runs
        )
        and time.time() < deadline
    ):
        time.sleep(0.01)
    service.shutdown()
    assert all(
        store.get_analysis_run(run["id"])["status"] == "completed"
        for run in runs
    )

"""Background execution for durable web analysis runs."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import threading
from typing import Any, Callable, Iterable

from .admin_store import AdminStore
from .events import AnalysisEvent
from .runner import AnalysisRequest, create_graph_for_request, stream_analysis_events


def _summarize_decision(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:120]
    return ""


class AnalysisTaskService:
    """Execute analysis runs independently of browser connections."""

    def __init__(
        self,
        store: AdminStore,
        graph_builder: Callable[[AnalysisRequest], Any] = create_graph_for_request,
        event_stream: Callable[
            [Any, AnalysisRequest], Iterable[AnalysisEvent]
        ] = stream_analysis_events,
        max_workers: int = 2,
    ):
        self.store = store
        self.graph_builder = graph_builder
        self.event_stream = event_stream
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="analysis",
        )
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, run_id: str) -> Future:
        with self._lock:
            existing = self._futures.get(str(run_id))
            if existing is not None and not existing.done():
                return existing
            future = self.executor.submit(self._execute, str(run_id))
            self._futures[str(run_id)] = future
            return future

    def recover(self) -> list[Future]:
        self.store.mark_interrupted_runs_failed()
        return [self.submit(run["id"]) for run in self.store.list_queued_analysis_runs()]

    def shutdown(self, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=False)

    def _execute(self, run_id: str) -> None:
        run = self.store.claim_analysis_run(run_id)
        if run is None:
            return

        request = AnalysisRequest(
            ticker=run["ticker"],
            trade_date=run["trade_date"],
            asset_type=run["asset_type"],
            analysts=tuple(run["analysts"]),
        )
        sections: dict[str, str] = {}
        terminal_event = ""

        try:
            graph = self.graph_builder(request)
            for event in self.event_stream(graph, request):
                if event.event == "report_section_updated":
                    section = str(event.data.get("section") or "")
                    content = str(event.data.get("content") or "")
                    if section and content.strip():
                        sections[section] = content
                    self.store.append_analysis_event(run_id, event)
                elif event.event == "run_failed":
                    terminal_event = event.event
                    self.store.fail_analysis_run(
                        run_id,
                        str(event.data.get("error_type") or "AnalysisError"),
                        str(event.data.get("message") or "分析失败"),
                    )
                    self.store.append_analysis_event(run_id, event)
                    return
                elif event.event == "run_completed":
                    terminal_event = event.event
                    report_id = None
                    if sections:
                        report = self.store.save_analysis_report(
                            {
                                "run_id": run_id,
                                "ticker": run["ticker"],
                                "stock_name": run["stock_name"],
                                "trade_date": run["trade_date"],
                                "analysts": run["analysts"],
                                "sections": sections,
                                "decision": _summarize_decision(
                                    sections.get("final_trade_decision", "")
                                ),
                                "owner_key": run["owner_key"],
                                "owner_uid": run["owner_uid"],
                                "owner_email": run["owner_email"],
                                "owner": run["owner_email"] or run["owner_uid"],
                            }
                        )
                        report_id = int(report["id"])
                    self.store.complete_analysis_run(run_id, report_id)
                    self.store.append_analysis_event(run_id, event)
                    return
                else:
                    self.store.append_analysis_event(run_id, event)
        except Exception as exc:  # noqa: BLE001 - persist all worker failures
            current = self.store.get_analysis_run(run_id)
            if current and current["status"] in {"completed", "failed"}:
                return
            event = AnalysisEvent(
                "run_failed",
                {"error_type": type(exc).__name__, "message": str(exc)},
            )
            self.store.append_analysis_event(run_id, event)
            self.store.fail_analysis_run(run_id, type(exc).__name__, str(exc))
            return

        if not terminal_event:
            message = "分析事件流未返回完成或失败状态。"
            self.store.append_analysis_event(
                run_id,
                AnalysisEvent(
                    "run_failed",
                    {"error_type": "ProtocolError", "message": message},
                ),
            )
            self.store.fail_analysis_run(run_id, "ProtocolError", message)


def create_task_service(store: AdminStore) -> AnalysisTaskService:
    return AnalysisTaskService(store)

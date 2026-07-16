"""Background execution for durable web analysis runs."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .events import AnalysisEvent
from .runner import AnalysisRequest, create_graph_for_request, stream_analysis_events
from .store import ApplicationStore

MAX_EXECUTOR_WORKERS = 8


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
        store: ApplicationStore,
        graph_builder: Callable[[AnalysisRequest], Any] = create_graph_for_request,
        event_stream: Callable[
            [Any, AnalysisRequest], Iterable[AnalysisEvent]
        ] = stream_analysis_events,
    ):
        self.store = store
        self.graph_builder = graph_builder
        self.event_stream = event_stream
        self.executor = ThreadPoolExecutor(
            max_workers=MAX_EXECUTOR_WORKERS,
            thread_name_prefix="analysis",
        )
        self._active: dict[str, Future] = {}
        self._condition = threading.Condition()
        self._started = False
        self._recovered = False
        self._stopping = False
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop,
            name="analysis-dispatcher",
            daemon=True,
        )

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            if not self._recovered:
                self.store.mark_interrupted_runs_failed()
                self._recovered = True
            self._started = True
            self._dispatcher.start()
            self._condition.notify_all()

    def notify(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def dispatch_once(self) -> int:
        with self._condition:
            if self._stopping:
                return 0
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
                self._submit_claimed(run)
                submitted += 1
            return submitted

    def _dispatch_loop(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    return
            submitted = self.dispatch_once()
            with self._condition:
                if self._stopping:
                    return
                self._condition.wait(timeout=0.05 if submitted else 0.5)

    def _submit_claimed(self, run: dict[str, Any]) -> Future:
        run_id = str(run["id"])
        future = self.executor.submit(self._execute_claimed, run)
        self._active[run_id] = future
        future.add_done_callback(
            lambda finished, current_run_id=run_id: self._finished(
                current_run_id,
                finished,
            )
        )
        return future

    def _finished(self, run_id: str, future: Future) -> None:
        with self._condition:
            self._active.pop(str(run_id), None)
            self._condition.notify_all()

    def submit(self, run_id: str) -> Future:
        with self._condition:
            existing = self._active.get(str(run_id))
            if existing is not None and not existing.done():
                return existing
            run = self.store.claim_analysis_run(str(run_id))
            if run is None:
                completed = Future()
                completed.set_result(None)
                return completed
            return self._submit_claimed(run)

    def recover(self) -> list[Future]:
        self.start()
        self.dispatch_once()
        with self._condition:
            return list(self._active.values())

    def shutdown(self, wait: bool = True) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._dispatcher.is_alive():
            self._dispatcher.join(timeout=5)
        self.executor.shutdown(wait=wait, cancel_futures=False)

    def _execute_claimed(self, run: dict[str, Any]) -> None:
        run_id = str(run["id"])
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


def create_task_service(store: ApplicationStore) -> AnalysisTaskService:
    return AnalysisTaskService(store)

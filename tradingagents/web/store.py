"""Shared storage contracts and task runtime settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class QueueLimitReached(RuntimeError):
    """Raised when the durable queued-task limit has been reached."""


class TaskSubmissionPaused(RuntimeError):
    """Raised when an administrator has paused new task submissions."""


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
    ) -> RuntimeSettings:
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
            analysis_concurrency_limit=concurrency,
            analysis_queue_limit=queue_limit,
            accept_new_tasks=accepting,
            updated_by=updated_by,
            updated_at=updated_at,
        )


class ApplicationStore(Protocol):
    """Behavior required by the Web API, runner, and task scheduler."""

    def ping(self) -> bool: ...

    def get_runtime_settings(self) -> RuntimeSettings: ...

    def update_runtime_settings(
        self,
        payload: dict[str, Any],
        updated_by: str,
    ) -> RuntimeSettings: ...

    def count_queued_analysis_runs(self) -> int: ...

    def count_running_analysis_runs(self) -> int: ...

    def claim_next_analysis_run(self) -> dict[str, Any] | None: ...

    def get_app_user(self, uid: str) -> dict[str, Any] | None: ...

    def list_app_users(self) -> list[dict[str, Any]]: ...

    def upsert_app_user(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def delete_app_user(self, uid: str) -> bool: ...

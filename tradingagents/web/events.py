"""Event primitives for browser-facing TradingAgents streams."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


@dataclass(frozen=True)
class AnalysisEvent:
    """A single analysis event suitable for JSON or Server-Sent Events."""

    event: str
    data: dict[str, Any] = field(default_factory=dict)

    def asdict(self) -> dict[str, Any]:
        return {"event": self.event, "data": self.data}


def sse_encode(event: AnalysisEvent) -> str:
    """Render an AnalysisEvent as one Server-Sent Events frame."""
    payload = json.dumps(event.asdict(), ensure_ascii=False, default=str)
    return f"event: {event.event}\ndata: {payload}\n\n"

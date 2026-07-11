"""Convert TradingAgents graph streams into browser-friendly events."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .admin_store import get_admin_store
from .events import AnalysisEvent


REPORT_SECTIONS: tuple[str, ...] = (
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
)


@dataclass(frozen=True)
class AnalysisRequest:
    ticker: str
    trade_date: str
    asset_type: str = "stock"
    analysts: tuple[str, ...] = ("market", "social", "news", "fundamentals")


def config_for_request(
    request: AnalysisRequest,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve web-run configuration, preferring China vendors for A shares."""
    from tradingagents.dataflows.china_symbol_utils import parse_china_symbol
    from tradingagents.default_config import DEFAULT_CONFIG, MARKET_PROFILES

    resolved = deepcopy(DEFAULT_CONFIG if config is None else config)
    is_a_share = request.asset_type == "stock" and parse_china_symbol(request.ticker)
    if not is_a_share or resolved.get("market_profile") == "china_mainland":
        return resolved

    for key, value in MARKET_PROFILES["china_mainland"].items():
        if isinstance(value, dict) and isinstance(resolved.get(key), dict):
            resolved[key] = {**resolved[key], **deepcopy(value)}
        else:
            resolved[key] = deepcopy(value)
    return resolved


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return " ".join(p.strip() for p in parts if p and p.strip())
    if isinstance(content, dict) and "text" in content:
        return str(content["text"]).strip()
    return str(content).strip()


def _tool_call_parts(tool_call: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(tool_call, dict):
        return str(tool_call.get("name", "")), dict(tool_call.get("args") or {})
    return str(getattr(tool_call, "name", "")), dict(getattr(tool_call, "args", {}) or {})


def _message_events(message: Any, seen_message_ids: set[str]) -> Iterable[AnalysisEvent]:
    msg_id = getattr(message, "id", None)
    if msg_id is not None:
        msg_key = str(msg_id)
        if msg_key in seen_message_ids:
            return
        seen_message_ids.add(msg_key)

    for tool_call in getattr(message, "tool_calls", []) or []:
        name, args = _tool_call_parts(tool_call)
        if name:
            yield AnalysisEvent("tool_called", {"tool": name, "args": args})

    text = _content_to_text(getattr(message, "content", None))
    if text:
        yield AnalysisEvent(
            "agent_message",
            {
                "message_type": type(message).__name__,
                "content": text,
            },
        )


def stream_analysis_events(
    graph: Any,
    request: AnalysisRequest,
    callbacks: list | None = None,
) -> Iterable[AnalysisEvent]:
    """Run a prepared TradingAgentsGraph-like object and yield AnalysisEvent values."""
    yield AnalysisEvent(
        "run_started",
        {
            "ticker": request.ticker,
            "trade_date": request.trade_date,
            "asset_type": request.asset_type,
            "analysts": list(request.analysts),
        },
    )

    instrument_context = graph.resolve_instrument_context(request.ticker, request.asset_type)
    initial_state = graph.propagator.create_initial_state(
        request.ticker,
        request.trade_date,
        asset_type=request.asset_type,
        instrument_context=instrument_context,
    )
    args = graph.propagator.get_graph_args(callbacks=callbacks)

    trace: list[dict[str, Any]] = []
    seen_message_ids: set[str] = set()
    seen_reports: dict[str, Any] = {}

    try:
        for chunk in graph.graph.stream(initial_state, **args):
            trace.append(chunk)
            for message in chunk.get("messages", []) or []:
                yield from _message_events(message, seen_message_ids)

            for section in REPORT_SECTIONS:
                content = chunk.get(section)
                if content and seen_reports.get(section) != content:
                    seen_reports[section] = content
                    yield AnalysisEvent(
                        "report_section_updated",
                        {"section": section, "content": content},
                    )
    except Exception as exc:  # noqa: BLE001 - errors must reach the browser as events
        yield AnalysisEvent(
            "run_failed",
            {
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
        )
        return

    final_state: dict[str, Any] = {}
    for chunk in trace:
        final_state.update(chunk)

    yield AnalysisEvent("run_completed", {"final_state": final_state})


def create_graph_for_request(
    request: AnalysisRequest,
    config: dict[str, Any] | None = None,
    graph_factory: Callable[..., Any] | None = None,
) -> Any:
    """Create TradingAgentsGraph lazily so importing web modules stays lightweight."""
    use_admin_runtime_config = graph_factory is None
    if graph_factory is None:
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph_factory = TradingAgentsGraph
    config = config_for_request(request, config)

    runtime_model = get_admin_store().get_default_runtime_model() if use_admin_runtime_config else None
    if runtime_model is not None:
        config = dict(config or {})
        config.update(
            {
                "llm_provider": runtime_model.provider,
                "quick_think_llm": runtime_model.quick_model,
                "deep_think_llm": runtime_model.deep_model,
                "backend_url": runtime_model.base_url,
                "api_key": runtime_model.api_key,
            }
        )

    return graph_factory(
        selected_analysts=list(request.analysts),
        config=config,
        debug=True,
    )

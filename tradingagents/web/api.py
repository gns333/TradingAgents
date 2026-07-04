"""FastAPI entry point for TradingAgents web progress viewing."""

from __future__ import annotations

from pathlib import Path

from .events import AnalysisEvent, sse_encode
from .runner import AnalysisRequest, create_graph_for_request, stream_analysis_events


STATIC_DIR = Path(__file__).with_name("static")


def create_app():
    """Create the optional FastAPI app.

    FastAPI is imported inside the factory so non-web users do not need the
    optional dependency merely to import the package.
    """
    try:
        from fastapi import FastAPI, Query
        from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
    except ImportError as exc:
        raise RuntimeError(
            "The TradingAgents web app requires optional dependencies. "
            "Install them with `pip install 'tradingagents[web]'`."
        ) from exc

    app = FastAPI(title="TradingAgents Web")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/events")
    def events(
        ticker: str = Query(..., min_length=1),
        trade_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
        asset_type: str = Query("stock"),
        analysts: str = Query("market,news,fundamentals"),
    ):
        selected = tuple(part.strip() for part in analysts.split(",") if part.strip())
        request = AnalysisRequest(
            ticker=ticker,
            trade_date=trade_date,
            asset_type=asset_type,
            analysts=selected or ("market", "news", "fundamentals"),
        )

        def body():
            try:
                graph = create_graph_for_request(request)
            except Exception as exc:  # noqa: BLE001 - return visible SSE error
                yield sse_encode(
                    AnalysisEvent(
                        "run_failed",
                        {
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        },
                    )
                )
                return
            for event in stream_analysis_events(graph, request):
                yield sse_encode(event)

        return StreamingResponse(body(), media_type="text/event-stream")

    return app


app = create_app()

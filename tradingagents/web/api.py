"""FastAPI entry point for TradingAgents web progress viewing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .admin_store import get_admin_store
from .events import AnalysisEvent, sse_encode
from .runner import AnalysisRequest, create_graph_for_request, stream_analysis_events


STATIC_DIR = Path(__file__).with_name("static")
ADMIN_HTML = STATIC_DIR / "admin.html"


def create_app():
    """Create the optional FastAPI app.

    FastAPI is imported inside the factory so non-web users do not need the
    optional dependency merely to import the package.
    """
    try:
        from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
        from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
    except ImportError as exc:
        raise RuntimeError(
            "The TradingAgents web app requires optional dependencies. "
            "Install them with `pip install 'tradingagents[web]'`."
        ) from exc
    globals()["Request"] = Request

    app = FastAPI(title="TradingAgents Web")
    store = get_admin_store()

    def _admin_token(request: Request) -> str | None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        return request.cookies.get("ta_admin")

    def require_admin(request: Request) -> None:
        if not store.verify_admin_session(_admin_token(request)):
            raise HTTPException(status_code=401, detail="admin login required")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/admin", response_class=HTMLResponse)
    def admin_index():
        return FileResponse(ADMIN_HTML)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/admin/status")
    def admin_status():
        return store.admin_status()

    def _password_from_payload(payload: dict[str, Any]) -> str:
        password = str(payload.get("password") or "")
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="admin password must be at least 8 characters")
        return password

    @app.post("/api/admin/setup")
    def admin_setup(payload: dict[str, Any] = Body(...)):
        if store.admin_password_is_configured():
            raise HTTPException(status_code=409, detail="admin password already configured")
        store.set_admin_password(_password_from_payload(payload))
        return {"ok": True}

    @app.post("/api/admin/login")
    def admin_login(payload: dict[str, Any] = Body(...)):
        if not store.verify_admin_password(_password_from_payload(payload)):
            raise HTTPException(status_code=401, detail="invalid admin password")
        return {"token": store.create_admin_session()}

    @app.get("/api/admin/whitelist")
    def list_whitelist(request: Request):
        require_admin(request)
        return {"items": store.list_whitelist()}

    @app.post("/api/admin/whitelist")
    def save_whitelist(request: Request, payload: dict[str, Any] = Body(...)):
        require_admin(request)
        try:
            item = store.upsert_whitelist(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"item": item}

    @app.delete("/api/admin/whitelist/{item_id}")
    def delete_whitelist(item_id: int, request: Request):
        require_admin(request)
        store.delete_whitelist(item_id)
        return {"ok": True}

    @app.get("/api/admin/model-configs")
    def list_model_configs(request: Request):
        require_admin(request)
        return {"items": store.list_model_configs()}

    @app.post("/api/admin/model-configs")
    def save_model_config(request: Request, payload: dict[str, Any] = Body(...)):
        require_admin(request)
        try:
            item = store.save_model_config(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"item": item}

    @app.post("/api/admin/model-configs/{item_id}/set-default")
    def set_default_model_config(item_id: int, request: Request):
        require_admin(request)
        try:
            store.set_default_model_config(item_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.delete("/api/admin/model-configs/{item_id}")
    def delete_model_config(item_id: int, request: Request):
        require_admin(request)
        store.delete_model_config(item_id)
        return {"ok": True}

    def _identity_from_request(
        access_email: str | None,
        x_cloudbase_uid: str | None,
        x_cloudbase_email: str | None,
        x_user_uid: str | None,
        x_user_email: str | None,
    ) -> dict[str, Any]:
        return {
            "uid": x_cloudbase_uid or x_user_uid or "",
            "email": x_cloudbase_email or x_user_email or access_email or "",
        }

    @app.get("/api/events")
    def events(
        ticker: str = Query(..., min_length=1),
        trade_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
        asset_type: str = Query("stock"),
        analysts: str = Query("market,news,fundamentals"),
        access_email: str | None = Query(None),
        x_cloudbase_uid: str | None = Header(None),
        x_cloudbase_email: str | None = Header(None),
        x_user_uid: str | None = Header(None),
        x_user_email: str | None = Header(None),
    ):
        selected = tuple(part.strip() for part in analysts.split(",") if part.strip())
        request = AnalysisRequest(
            ticker=ticker,
            trade_date=trade_date,
            asset_type=asset_type,
            analysts=selected or ("market", "news", "fundamentals"),
        )

        def body():
            identity = _identity_from_request(
                access_email,
                x_cloudbase_uid,
                x_cloudbase_email,
                x_user_uid,
                x_user_email,
            )
            if not store.is_identity_allowed(identity["email"], identity["uid"]):
                yield sse_encode(
                    AnalysisEvent(
                        "run_failed",
                        {
                            "error_type": "AccessDenied",
                            "message": "当前账号不在白名单中，无法发起分析。",
                        },
                    )
                )
                return
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

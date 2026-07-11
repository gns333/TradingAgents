"""FastAPI entry point for TradingAgents web progress viewing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .admin_store import get_admin_store
from .events import AnalysisEvent, sse_encode
from .runner import AnalysisRequest, create_graph_for_request, stream_analysis_events
from .stock_directory import get_stock_directory


def _summarize_decision(text: str) -> str:
    """Reduce a final-decision report to a short, list-friendly summary line."""
    for line in str(text or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:120]
    return ""


STATIC_DIR = Path(__file__).with_name("static")
ADMIN_HTML = STATIC_DIR / "admin.html"


def create_app():
    """Create the optional FastAPI app.

    FastAPI is imported inside the factory so non-web users do not need the
    optional dependency merely to import the package.
    """
    try:
        from fastapi import Body, FastAPI, Header, HTTPException, Query, Request, Response
        from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:
        raise RuntimeError(
            "The TradingAgents web app requires optional dependencies. "
            "Install them with `pip install 'tradingagents[web]'`."
        ) from exc
    globals()["Request"] = Request
    globals()["Response"] = Response

    app = FastAPI(title="TradingAgents Web")
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")
    store = get_admin_store()

    @app.middleware("http")
    async def no_cache_local_workbench_assets(request: Request, call_next):
        response = await call_next(request)
        if request.url.path in {"/", "/admin"} or request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def _admin_token(request: Request) -> str | None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        return request.cookies.get("ta_admin")

    def require_admin(request: Request) -> None:
        if not store.verify_admin_session(_admin_token(request)):
            raise HTTPException(status_code=401, detail="admin login required")

    def _set_admin_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            key="ta_admin",
            value=token,
            path="/",
            samesite="lax",
        )

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/admin", response_class=HTMLResponse)
    def admin_index():
        return FileResponse(ADMIN_HTML)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/stocks/search")
    def search_stocks(q: str = Query("", min_length=0), limit: int = Query(10, ge=1, le=50)):
        return {"items": get_stock_directory().search(q, limit)}

    @app.get("/api/reports")
    def list_reports():
        return {"items": store.list_analysis_reports()}

    @app.get("/api/reports/{item_id}")
    def get_report(item_id: int):
        report = store.get_analysis_report(item_id)
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        return {"item": report}

    @app.delete("/api/reports/{item_id}")
    def delete_report(item_id: int):
        store.delete_analysis_report(item_id)
        return {"ok": True}

    @app.get("/api/admin/status")
    def admin_status(request: Request, response: Response):
        token = _admin_token(request)
        session_valid = store.verify_admin_session(token)
        if session_valid and token:
            _set_admin_cookie(response, token)
        elif token:
            response.delete_cookie("ta_admin", path="/")
        return {
            **store.admin_status(),
            "session_valid": session_valid,
        }

    def _password_from_payload(payload: dict[str, Any]) -> str:
        password = str(payload.get("password") or "")
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="管理员密码至少需要 8 位")
        return password

    @app.post("/api/admin/setup")
    def admin_setup(payload: dict[str, Any] = Body(...)):
        if store.admin_password_is_configured():
            raise HTTPException(status_code=409, detail="admin password already configured")
        store.set_admin_password(_password_from_payload(payload))
        return {"ok": True}

    @app.post("/api/admin/login")
    def admin_login(response: Response, payload: dict[str, Any] = Body(...)):
        if not store.verify_admin_password(_password_from_payload(payload)):
            raise HTTPException(status_code=401, detail="invalid admin password")
        token = store.create_admin_session()
        _set_admin_cookie(response, token)
        return {"token": token}

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

    def _require_allowed_identity(
        identity: dict[str, Any], action: str, request: Request
    ) -> None:
        if store.verify_admin_session(_admin_token(request)):
            return
        if not store.is_identity_allowed(identity["email"], identity["uid"]):
            raise HTTPException(status_code=403, detail=f"当前账号不在白名单中，无法发起{action}。")

    @app.get("/api/events")
    def events(
        request: Request,
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
        request_model = AnalysisRequest(
            ticker=ticker,
            trade_date=trade_date,
            asset_type=asset_type,
            analysts=selected or ("market", "news", "fundamentals"),
        )
        is_admin = store.verify_admin_session(_admin_token(request))

        def body():
            identity = _identity_from_request(
                access_email,
                x_cloudbase_uid,
                x_cloudbase_email,
                x_user_uid,
                x_user_email,
            )
            if not is_admin and not store.is_identity_allowed(
                identity["email"], identity["uid"]
            ):
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
                graph = create_graph_for_request(request_model)
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
            collected_sections: dict[str, str] = {}
            for event in stream_analysis_events(graph, request_model):
                if event.event == "report_section_updated":
                    section = str(event.data.get("section") or "")
                    if section:
                        collected_sections[section] = str(event.data.get("content") or "")
                elif event.event == "run_completed" and collected_sections:
                    try:
                        store.save_analysis_report(
                            {
                                "ticker": request_model.ticker,
                                "trade_date": request_model.trade_date,
                                "analysts": list(request_model.analysts),
                                "sections": collected_sections,
                                "decision": _summarize_decision(
                                    collected_sections.get("final_trade_decision", "")
                                ),
                                "owner": identity.get("email") or identity.get("uid") or "",
                            }
                        )
                    except Exception:  # noqa: BLE001 - persistence must not break the stream
                        pass
                yield sse_encode(event)

        return StreamingResponse(body(), media_type="text/event-stream")

    return app


app = create_app()

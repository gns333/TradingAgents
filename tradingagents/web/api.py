"""FastAPI entry point for TradingAgents web progress viewing."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

from .admin_store import ActiveRunExists, get_admin_store
from .events import AnalysisEvent, sse_encode
from .identity import IdentityRequired, Principal
from .model_catalog import CatalogUnsupported, ModelCatalogError, get_model_catalog
from .stock_directory import get_stock_directory
from .task_service import create_task_service

STATIC_DIR = Path(__file__).with_name("static")
ADMIN_HTML = STATIC_DIR / "admin.html"


def create_app():
    """Create the optional FastAPI app.

    FastAPI is imported inside the factory so non-web users do not need the
    optional dependency merely to import the package.
    """
    try:
        from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
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
    task_service = create_task_service(store)
    app.state.task_service = task_service

    @app.on_event("startup")
    def recover_analysis_tasks():
        task_service.recover()

    @app.on_event("shutdown")
    def stop_analysis_tasks():
        task_service.shutdown(wait=True)

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

    def _principal_from_request(
        request: Request,
        access_email: str | None = None,
    ) -> Principal:
        is_admin = store.verify_admin_session(_admin_token(request))
        if not store.admin_password_is_configured():
            is_admin = True
        try:
            return Principal.from_values(
                uid=(
                    request.headers.get("x-cloudbase-uid")
                    or request.headers.get("x-user-uid")
                ),
                email=(
                    request.headers.get("x-cloudbase-email")
                    or request.headers.get("x-user-email")
                    or access_email
                ),
                is_admin=is_admin,
            )
        except IdentityRequired as exc:
            raise HTTPException(
                status_code=401,
                detail={
                    "error_type": "IdentityRequired",
                    "message": "请先登录或提供有效访问身份。",
                },
            ) from exc

    def _require_allowed_principal(principal: Principal, action: str) -> None:
        if principal.is_admin:
            return
        if not store.is_identity_allowed(principal.email, principal.uid):
            raise HTTPException(
                status_code=403,
                detail={
                    "error_type": "AccessDenied",
                    "message": f"当前账号不在白名单中，无法发起{action}。",
                },
            )

    def _require_owned_run(run_id: str, principal: Principal) -> dict[str, Any]:
        run = store.get_analysis_run(run_id)
        if run is None or (
            not principal.is_admin and run["owner_key"] != principal.owner_key
        ):
            raise HTTPException(status_code=404, detail="analysis run not found")
        return run

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
    def list_reports(request: Request, access_email: str | None = Query(None)):
        principal = _principal_from_request(request, access_email)
        owner_key = None if principal.is_admin else principal.owner_key
        return {"items": store.list_analysis_reports(owner_key=owner_key)}

    @app.get("/api/reports/{item_id}")
    def get_report(
        item_id: int,
        request: Request,
        access_email: str | None = Query(None),
    ):
        principal = _principal_from_request(request, access_email)
        owner_key = None if principal.is_admin else principal.owner_key
        report = store.get_analysis_report(item_id, owner_key=owner_key)
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        return {"item": report}

    @app.delete("/api/reports/{item_id}")
    def delete_report(
        item_id: int,
        request: Request,
        access_email: str | None = Query(None),
    ):
        principal = _principal_from_request(request, access_email)
        owner_key = None if principal.is_admin else principal.owner_key
        if not store.delete_analysis_report(item_id, owner_key=owner_key):
            raise HTTPException(status_code=404, detail="report not found")
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

    @app.post("/api/admin/model-catalog")
    def fetch_model_catalog(request: Request, payload: dict[str, Any] = Body(...)):
        require_admin(request)
        provider = str(payload.get("provider") or "").strip().lower()
        api_key = str(payload.get("api_key") or "")
        base_url = str(payload.get("base_url") or "").strip() or None
        config_id = payload.get("config_id")
        if config_id and not api_key:
            runtime = store.get_runtime_model_config(int(config_id))
            if runtime is None:
                raise HTTPException(status_code=404, detail="model config not found")
            provider = provider or runtime.provider
            api_key = runtime.api_key
            base_url = base_url or runtime.base_url
        if not provider:
            raise HTTPException(status_code=400, detail="provider is required")
        try:
            models = get_model_catalog().fetch(provider, api_key, base_url)
        except CatalogUnsupported as exc:
            return {
                "models": [],
                "source": "manual",
                "message": str(exc),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        except ModelCatalogError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            ) from exc
        return {
            "models": [model.as_dict() for model in models],
            "source": "provider_api",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

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

    @app.post("/api/runs", status_code=201)
    def create_run(
        request: Request,
        payload: dict[str, Any] = Body(...),
        access_email: str | None = Query(None),
    ):
        principal = _principal_from_request(request, access_email)
        _require_allowed_principal(principal, "分析")
        ticker = str(payload.get("ticker") or "").strip()
        trade_date = str(payload.get("trade_date") or "").strip()
        if not ticker or not trade_date:
            raise HTTPException(status_code=400, detail="ticker and trade_date are required")
        analysts = [
            str(item).strip()
            for item in (payload.get("analysts") or ["market", "news", "fundamentals"])
            if str(item).strip()
        ]
        stock_name = str(payload.get("stock_name") or "").strip()
        try:
            for item in get_stock_directory().search(ticker, 10):
                if item["code"].upper() == ticker.upper():
                    stock_name = item["name"]
                    break
        except Exception:  # noqa: BLE001 - name lookup must not block analysis
            pass
        try:
            run = store.create_analysis_run(
                {
                    "owner_key": principal.owner_key,
                    "owner_uid": principal.uid,
                    "owner_email": principal.email,
                    "ticker": ticker,
                    "stock_name": stock_name,
                    "trade_date": trade_date,
                    "asset_type": str(payload.get("asset_type") or "stock"),
                    "analysts": analysts,
                }
            )
        except ActiveRunExists as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_type": "ActiveRunExists",
                    "message": "当前用户已有正在执行或排队中的分析任务。",
                    "run": exc.run,
                },
            ) from exc
        task_service.submit(run["id"])
        return {"run": run}

    @app.get("/api/runs/active")
    def get_active_run(
        request: Request,
        access_email: str | None = Query(None),
        owner_key: str | None = Query(None),
    ):
        principal = _principal_from_request(request, access_email)
        lookup_owner = (
            str(owner_key).strip()
            if principal.is_admin and owner_key
            else principal.owner_key
        )
        return {"run": store.get_active_analysis_run(lookup_owner)}

    @app.get("/api/runs/{run_id}")
    def get_run(
        run_id: str,
        request: Request,
        access_email: str | None = Query(None),
    ):
        principal = _principal_from_request(request, access_email)
        return {"run": _require_owned_run(run_id, principal)}

    @app.get("/api/runs/{run_id}/events")
    def get_run_events(
        run_id: str,
        request: Request,
        after: int = Query(0, ge=0),
        access_email: str | None = Query(None),
    ):
        principal = _principal_from_request(request, access_email)
        _require_owned_run(run_id, principal)
        return {"items": store.list_analysis_events(run_id, after=after)}

    @app.get("/api/runs/{run_id}/stream")
    def stream_run_events(
        run_id: str,
        request: Request,
        after: int = Query(0, ge=0),
        access_email: str | None = Query(None),
    ):
        principal = _principal_from_request(request, access_email)
        _require_owned_run(run_id, principal)

        def body():
            cursor = int(after)
            while True:
                items = store.list_analysis_events(run_id, after=cursor)
                for item in items:
                    cursor = int(item["seq"])
                    data = dict(item["data"])
                    data.update({"seq": cursor, "run_id": run_id})
                    yield sse_encode(AnalysisEvent(item["event"], data))
                run = store.get_analysis_run(run_id)
                if run is None or run["status"] in {"completed", "failed"}:
                    if not store.list_analysis_events(run_id, after=cursor):
                        return
                if not items:
                    yield ": keep-alive\n\n"
                time.sleep(0.5)

        return StreamingResponse(body(), media_type="text/event-stream")

    return app


app = create_app()

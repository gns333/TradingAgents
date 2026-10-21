"""CloudBase personal-plan store: HTTPS NoSQL, no VPC or local cloud files.

The small control document serializes queue admission and integer ID allocation.
Reports/events remain separate documents; no unbounded database snapshot is kept.
Run claim, owner lock and queue state always change in one database transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from .admin_store import ActiveRunExists, _b64, _unb64, mask_secret
from .document_client import DocumentClient
from .mysql_store import MySQLApplicationStore
from .store import QueueLimitReached, RuntimeSettings, TaskSubmissionPaused

COLLECTIONS = (
    "ta_control", "ta_users", "ta_models", "ta_whitelist", "ta_sessions",
    "ta_runs", "ta_events", "ta_reports", "ta_owners", "ta_operations",
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _key(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


class CloudBaseDocumentStore:
    # Reuse only transport-independent encryption/DTO conversion, never SQL.
    encrypt_secret = MySQLApplicationStore.encrypt_secret
    decrypt_secret = MySQLApplicationStore.decrypt_secret
    _model_row = MySQLApplicationStore._model_row
    _runtime_model_row = MySQLApplicationStore._runtime_model_row
    dispatcher_idle_seconds = 5.0

    def __init__(self, env_id, api_key, master_key, *, client=None):
        if len(master_key) != 32:
            raise ValueError("master_key must be exactly 32 bytes")
        self.master_key = master_key
        self.db = client or DocumentClient(env_id, api_key)
        self._settings_cache = None
        self._settings_until = 0.0
        # Collections and private security rules are provisioned before deployment.
        self.db.atomic(self._initialize)

    def _initialize(self, tx):
        if self.db.get("ta_control", "state", tx) is None:
            self.db.put("ta_control", "state", {
                "settings": asdict(RuntimeSettings()), "queue": [], "running": [],
                "counters": {}, "default_model_id": None,
            }, tx)

    def _control(self, tx=None):
        row = self.db.get("ta_control", "state", tx)
        if row is None:
            raise RuntimeError("CloudBase store is not initialized")
        return row

    def _write_control(self, control, tx):
        self.db.put("ta_control", "state", control, tx)

    def _next_id(self, control, name):
        value = int(control["counters"].get(name, 0)) + 1
        control["counters"][name] = value
        return value

    def ping(self):
        try:
            return self.db.get("ta_control", "state") is not None
        except Exception:
            return False

    def get_runtime_settings(self):
        if self._settings_cache is None or time.monotonic() >= self._settings_until:
            data = self._control()["settings"]
            self._settings_cache = RuntimeSettings.from_payload(
                data, updated_by=data.get("updated_by", ""),
                updated_at=data.get("updated_at", ""),
            )
            self._settings_until = time.monotonic() + 5
        return self._settings_cache

    def update_runtime_settings(self, payload, updated_by):
        settings = RuntimeSettings.from_payload(
            payload, updated_by=str(updated_by), updated_at=_now(),
        )

        def update(tx):
            control = self._control(tx)
            control["settings"] = asdict(settings)
            self._write_control(control, tx)

        self.db.atomic(update)
        self._settings_cache = None
        return settings

    def admin_password_is_configured(self):
        return bool(self._control().get("admin_password_hash"))

    def set_admin_password(self, password):
        if len(password) < 8:
            raise ValueError("admin password must be at least 8 characters")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)

        def update(tx):
            control = self._control(tx)
            control["admin_password_hash"] = _b64(salt) + ":" + _b64(digest)
            self._write_control(control, tx)

        self.db.atomic(update)

    def verify_admin_password(self, password):
        stored = self._control().get("admin_password_hash")
        if not stored:
            return False
        salt, digest = stored.split(":", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), 210_000)
        return hmac.compare_digest(actual, _unb64(digest))

    def create_admin_session(self):
        token = _b64(secrets.token_bytes(32))
        self.db.put("ta_sessions", _key(token), {"created_at": _now()})
        return token

    def verify_admin_session(self, token):
        return bool(token and self.db.get("ta_sessions", _key(token)))

    def admin_status(self):
        return {"password_configured": self.admin_password_is_configured(),
                "warning": "API keys are encrypted with the CloudBase service master key."}

    def list_whitelist(self):
        return self.db.query("ta_whitelist", order=[("email", "asc")])

    def upsert_whitelist(self, payload):
        email = str(payload.get("email") or "").strip().lower()
        if "@" not in email:
            raise ValueError("valid email is required")
        status = str(payload.get("status") or "active")
        if status not in {"active", "blocked", "pending"}:
            raise ValueError("status must be active, blocked, or pending")

        def update(tx):
            control = self._control(tx)
            existing = self.db.get("ta_whitelist", _key(email), tx)
            row = {
                "id": existing["id"] if existing else self._next_id(control, "whitelist"),
                "email": email, "uid": str(payload.get("uid") or "").strip(),
                "status": status, "daily_limit": int(payload.get("daily_limit") or 5),
                "allowed_models": payload.get("allowed_models") or [],
                "note": str(payload.get("note") or ""),
                "created_at": existing["created_at"] if existing else _now(),
                "updated_at": _now(),
            }
            self.db.put("ta_whitelist", _key(email), row, tx)
            self._write_control(control, tx)
            return row["id"]

        self.db.atomic(update)
        return self.db.get("ta_whitelist", _key(email))

    def delete_whitelist(self, item_id):
        for row in self.db.query("ta_whitelist", {"id": int(item_id)}, limit=1):
            self.db.delete("ta_whitelist", _key(row["email"]))

    def is_identity_allowed(self, email=None, uid=None):
        if not self.admin_password_is_configured():
            return True
        query = {"$or": [{"email": (email or "").strip().lower() or "!"},
                          {"uid": (uid or "").strip() or "!"}]}
        return any(row["status"] == "active" for row in self.db.query("ta_whitelist", query))

    def get_app_user(self, uid):
        return self.db.get("ta_users", _key(str(uid).strip()))

    def list_app_users(self):
        return self.db.query("ta_users", order=[("role", "asc"), ("email", "asc"), ("uid", "asc")])

    def upsert_app_user(self, payload):
        uid = str(payload.get("uid") or "").strip()
        role = str(payload.get("role") or "user").strip().lower()
        status = str(payload.get("status") or "active").strip().lower()
        daily_limit = int(payload.get("daily_limit", 5))
        if not uid:
            raise ValueError("uid is required")
        if role not in {"admin", "user"} or status not in {"active", "disabled"}:
            raise ValueError("invalid user role or status")
        if daily_limit < 0:
            raise ValueError("daily_limit must be non-negative")

        def update(tx):
            existing = self.db.get("ta_users", _key(uid), tx)
            self.db.put("ta_users", _key(uid), {
                "uid": uid, "email": str(payload.get("email") or "").strip().lower(),
                "display_name": str(payload.get("display_name") or "").strip(),
                "role": role, "status": status, "daily_limit": daily_limit,
                "created_at": existing["created_at"] if existing else _now(),
                "updated_at": _now(),
            }, tx)

        self.db.atomic(update)
        return self.get_app_user(uid)

    def delete_app_user(self, uid):
        return self.db.delete("ta_users", _key(str(uid).strip()))

    def list_model_configs(self):
        default_id = self._control().get("default_model_id")
        rows = self.db.query("ta_models", order=[("id", "desc")])
        for row in rows:
            row["is_default"] = row["id"] == default_id
        rows.sort(key=lambda row: (row["is_default"], row["enabled"], row["id"]), reverse=True)
        return [self._model_row(row) for row in rows]

    def save_model_config(self, payload):
        provider = str(payload.get("provider") or "").strip().lower()
        quick = str(payload.get("quick_model") or "").strip()
        deep = str(payload.get("deep_model") or "").strip()
        api_key = str(payload.get("api_key") or "")
        if not provider or not quick or not deep:
            raise ValueError("provider, quick_model and deep_model are required")
        if not api_key and not payload.get("id"):
            raise ValueError("api_key is required for new model configs")

        def update(tx):
            control = self._control(tx)
            item_id = int(payload["id"]) if payload.get("id") else self._next_id(control, "models")
            existing = self.db.get("ta_models", str(item_id), tx)
            if payload.get("id") and existing is None:
                raise ValueError("model config not found")
            if api_key:
                ciphertext, nonce = self.encrypt_secret(api_key)
                masked = mask_secret(api_key)
            else:
                ciphertext, nonce = existing["api_key_ciphertext"], existing["api_key_nonce"]
                masked = existing["api_key_masked"]
            row = {
                "id": item_id, "provider": provider,
                "display_name": str(payload.get("display_name") or provider).strip(),
                "quick_model": quick, "deep_model": deep,
                "base_url": str(payload.get("base_url") or "").strip() or None,
                "api_key_ciphertext": ciphertext, "api_key_nonce": nonce,
                "api_key_masked": masked, "enabled": bool(payload.get("enabled", True)),
                "is_default": bool(payload.get("is_default", False)),
                "created_by": (existing or {}).get("created_by", str(payload.get("created_by") or "")),
                "created_at": (existing or {}).get("created_at", _now()), "updated_at": _now(),
            }
            if row["is_default"]:
                control["default_model_id"] = item_id
            elif control.get("default_model_id") == item_id:
                control["default_model_id"] = None
            self.db.put("ta_models", str(item_id), row, tx)
            self._write_control(control, tx)
            return item_id

        item_id = self.db.atomic(update)
        row = self.db.get("ta_models", str(item_id))
        row["is_default"] = self._control().get("default_model_id") == item_id
        return self._model_row(row)

    def set_default_model_config(self, item_id):
        def update(tx):
            control = self._control(tx)
            model = self.db.get("ta_models", str(int(item_id)), tx)
            if model is None or not model["enabled"]:
                raise ValueError("enabled model config not found")
            control["default_model_id"] = int(item_id)
            self._write_control(control, tx)

        self.db.atomic(update)

    def delete_model_config(self, item_id):
        def update(tx):
            control = self._control(tx)
            self.db.delete("ta_models", str(int(item_id)), tx)
            if control.get("default_model_id") == int(item_id):
                control["default_model_id"] = None
            self._write_control(control, tx)

        self.db.atomic(update)

    def get_default_runtime_model(self):
        default_id = self._control().get("default_model_id")
        if default_id:
            model = self.get_runtime_model_config(default_id)
            row = self.db.get("ta_models", str(default_id))
            if row and row["enabled"]:
                return model
        rows = self.db.query("ta_models", {"enabled": True}, order=[("id", "desc")], limit=1)
        return self._runtime_model_row(rows[0]) if rows else None

    def get_runtime_model_config(self, item_id):
        row = self.db.get("ta_models", str(int(item_id)))
        return self._runtime_model_row(row) if row else None

    def create_analysis_run(self, payload):
        for field in ("owner_key", "ticker", "trade_date"):
            if not str(payload.get(field) or "").strip():
                raise ValueError(field + " is required")
        run_id = str(payload.get("id") or uuid.uuid4())
        owner = str(payload["owner_key"]).strip()

        def create(tx):
            control = self._control(tx)
            settings = RuntimeSettings.from_payload(control["settings"])
            existing = self.db.get("ta_runs", run_id, tx)
            if existing is not None:
                if existing["owner_key"] != owner:
                    raise ValueError("analysis run ID already exists")
                return run_id
            active = self.db.get("ta_owners", _key(owner), tx)
            if active:
                raise ActiveRunExists(self.db.get("ta_runs", active["run_id"], tx))
            if not settings.accept_new_tasks:
                raise TaskSubmissionPaused("new analysis submissions are paused")
            if len(control["queue"]) >= settings.analysis_queue_limit:
                raise QueueLimitReached("analysis queue limit reached")
            row = {field: str(payload.get(field) or "").strip() for field in (
                "owner_uid", "owner_email", "ticker", "stock_name", "trade_date",
            )}
            row.update({"id": run_id, "owner_key": owner,
                        "owner_email": row["owner_email"].lower(),
                        "asset_type": str(payload.get("asset_type") or "stock"),
                        "analysts": payload.get("analysts") or [], "status": "queued",
                        "created_at": _now(), "started_at": None, "finished_at": None,
                        "heartbeat_at": None, "current_agent": "", "error_type": "",
                        "error_message": "", "last_event_seq": 0, "report_id": None})
            self.db.put("ta_runs", run_id, row, tx)
            self.db.put("ta_owners", _key(owner), {"run_id": run_id}, tx)
            control["queue"].append(run_id)
            self._write_control(control, tx)
            return run_id

        return self.get_analysis_run(self.db.atomic(create))

    def _claim(self, tx, run_id=None):
        control = self._control(tx)
        if run_id is None:
            run_id = control["queue"][0] if control["queue"] else None
        if not run_id or run_id not in control["queue"]:
            return None
        row = self.db.get("ta_runs", run_id, tx)
        if row is None or row["status"] != "queued":
            raise RuntimeError("Queue state does not match persisted run")
        row.update(status="running", started_at=row["started_at"] or _now(),
                   heartbeat_at=_now(), error_type="", error_message="")
        control["queue"].remove(run_id)
        control["running"].append(run_id)
        self.db.put("ta_runs", run_id, row, tx)
        self._write_control(control, tx)
        return run_id

    def claim_analysis_run(self, run_id):
        claimed = self.db.atomic(lambda tx: self._claim(tx, str(run_id)))
        return self.get_analysis_run(claimed) if claimed else None

    def claim_next_analysis_run(self):
        # Avoid empty transactions/receipts while the dispatcher is idle.
        if not self._control()["queue"]:
            return None
        claimed = self.db.atomic(self._claim)
        return self.get_analysis_run(claimed) if claimed else None

    def count_queued_analysis_runs(self):
        return len(self._control()["queue"])

    def count_running_analysis_runs(self):
        return len(self._control()["running"])

    def get_analysis_run(self, run_id):
        return self.db.get("ta_runs", str(run_id))

    def get_active_analysis_run(self, owner_key):
        owner = self.db.get("ta_owners", _key(owner_key))
        return self.get_analysis_run(owner["run_id"]) if owner else None

    def list_queued_analysis_runs(self):
        return self.db.query("ta_runs", {"status": "queued"}, order=[("created_at", "asc"), ("id", "asc")])

    def touch_analysis_run(self, run_id, current_agent=""):
        def update(tx):
            row = self.db.get("ta_runs", str(run_id), tx)
            if row:
                row.update(heartbeat_at=_now(), current_agent=str(current_agent))
                self.db.put("ta_runs", str(run_id), row, tx)

        self.db.atomic(update)

    def append_analysis_event(self, run_id, event):
        event_name = str(getattr(event, "event", "") or "")
        data = dict(getattr(event, "data", {}) or {})
        if not event_name:
            raise ValueError("event name is required")
        now = _now()

        def append(tx):
            run = self.db.get("ta_runs", str(run_id), tx)
            if run is None:
                raise ValueError("analysis run not found")
            seq = int(run["last_event_seq"]) + 1
            run.update(last_event_seq=seq, heartbeat_at=now,
                       current_agent=str(data.get("agent") or data.get("current_agent") or ""))
            self.db.put("ta_events", f"{run_id}-{seq}", {
                "run_id": str(run_id), "seq": seq, "event": event_name,
                "data": data, "created_at": now,
            }, tx)
            self.db.put("ta_runs", str(run_id), run, tx)
            return seq

        seq = self.db.atomic(append)
        return {"run_id": str(run_id), "seq": seq, "event": event_name,
                "data": data, "created_at": now}

    def list_analysis_events(self, run_id, after=0):
        return self.db.query("ta_events", {"run_id": str(run_id), "seq": {"$gt": int(after)}},
                             order=[("seq", "asc")])

    def _finish(self, run_id, status, **values):
        def finish(tx):
            control = self._control(tx)
            row = self.db.get("ta_runs", str(run_id), tx)
            if row is None:
                return
            row.update(status=status, finished_at=_now(), heartbeat_at=_now(), current_agent="", **values)
            for name in ("queue", "running"):
                if str(run_id) in control[name]:
                    control[name].remove(str(run_id))
            owner = self.db.get("ta_owners", _key(row["owner_key"]), tx)
            if owner and owner["run_id"] == str(run_id):
                self.db.delete("ta_owners", _key(row["owner_key"]), tx)
            self.db.put("ta_runs", str(run_id), row, tx)
            self._write_control(control, tx)

        self.db.atomic(finish)

    def complete_analysis_run(self, run_id, report_id=None):
        self._finish(run_id, "completed", report_id=report_id)

    def fail_analysis_run(self, run_id, error_type, error_message):
        self._finish(run_id, "failed", error_type=str(error_type), error_message=str(error_message))

    def mark_interrupted_runs_failed(self):
        running = self._control()["running"]
        for run_id in running:
            self.fail_analysis_run(run_id, "WorkerInterrupted", "分析服务重启，运行中的任务已中断。")
        return len(running)

    def save_analysis_report(self, payload):
        ticker = str(payload.get("ticker") or "").strip()
        sections = payload.get("sections") or {}
        if not ticker:
            raise ValueError("ticker is required")
        if not isinstance(sections, dict):
            raise ValueError("sections must be a mapping of section -> content")

        def save(tx):
            control = self._control(tx)
            item_id = self._next_id(control, "reports")
            row = {field: str(payload.get(field) or "").strip() for field in (
                "trade_date", "decision", "owner", "stock_name", "owner_key", "owner_uid",
            )}
            row.update({"id": item_id, "ticker": ticker, "analysts": payload.get("analysts") or [],
                        "sections": {str(k): str(v) for k, v in sections.items() if v and str(v).strip()},
                        "created_at": _now(), "run_id": str(payload.get("run_id") or "").strip() or None,
                        "owner_email": str(payload.get("owner_email") or payload.get("owner") or "").strip().lower()})
            row["section_keys"] = list(row["sections"])
            self.db.put("ta_reports", str(item_id), row, tx)
            self._write_control(control, tx)
            return item_id

        return self.get_analysis_report(self.db.atomic(save))

    def list_analysis_reports(self, limit=100, owner_key=None):
        query = {} if owner_key is None else {"owner_key": str(owner_key)}
        return self.db.query("ta_reports", query, order=[("id", "desc")],
                             limit=max(0, int(limit)), projection={"sections": 0})

    def get_analysis_report(self, item_id, owner_key=None):
        row = self.db.get("ta_reports", str(int(item_id)))
        if row is None or (owner_key is not None and row["owner_key"] != str(owner_key)):
            return None
        return row

    def delete_analysis_report(self, item_id, owner_key=None):
        def delete(tx):
            row = self.db.get("ta_reports", str(int(item_id)), tx)
            if row is None or (owner_key is not None and row["owner_key"] != str(owner_key)):
                return False
            return self.db.delete("ta_reports", str(int(item_id)), tx)

        return self.db.atomic(delete)

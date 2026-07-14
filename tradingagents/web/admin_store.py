"""Local admin configuration store for the optional web app.

This is intentionally a small repository-style layer. The default implementation
uses SQLite so the app can run locally without CloudBase credentials; the public
API is narrow enough to replace with a CloudBase DB-backed implementation later.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


DEFAULT_ADMIN_DB = Path(".tradingagents") / "web_admin.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data.encode("ascii"))


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return value[:1] + "***" + value[-1:]
    return value[:4] + "****" + value[-4:]


@dataclass(frozen=True)
class RuntimeModelConfig:
    provider: str
    quick_model: str
    deep_model: str
    api_key: str
    base_url: str | None = None


class ActiveRunExists(RuntimeError):
    """Raised when an owner already has a queued or running analysis."""

    def __init__(self, run: dict[str, Any]):
        super().__init__("owner already has an active analysis run")
        self.run = run


class AdminStore:
    def __init__(self, db_path: str | Path = DEFAULT_ADMIN_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS admin_sessions (
                    token_hash TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    expires_at TEXT
                );

                CREATE TABLE IF NOT EXISTS access_whitelist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    uid TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    daily_limit INTEGER NOT NULL DEFAULT 5,
                    allowed_models TEXT NOT NULL DEFAULT '[]',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    base_url TEXT,
                    quick_model TEXT NOT NULL,
                    deep_model TEXT NOT NULL,
                    api_key_ciphertext TEXT NOT NULL,
                    api_key_nonce TEXT NOT NULL,
                    api_key_masked TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS analysis_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    analysts TEXT NOT NULL DEFAULT '[]',
                    sections TEXT NOT NULL DEFAULT '{}',
                    decision TEXT NOT NULL DEFAULT '',
                    owner TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS analysis_runs (
                    id TEXT PRIMARY KEY,
                    owner_key TEXT NOT NULL,
                    owner_uid TEXT NOT NULL DEFAULT '',
                    owner_email TEXT NOT NULL DEFAULT '',
                    ticker TEXT NOT NULL,
                    stock_name TEXT NOT NULL DEFAULT '',
                    trade_date TEXT NOT NULL,
                    asset_type TEXT NOT NULL DEFAULT 'stock',
                    analysts TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'queued',
                    current_agent TEXT NOT NULL DEFAULT '',
                    last_event_seq INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    report_id INTEGER,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    heartbeat_at TEXT
                );

                CREATE TABLE IF NOT EXISTS analysis_run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    data TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, seq)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_runs_active_owner
                ON analysis_runs(owner_key)
                WHERE status IN ('queued', 'running');

                CREATE INDEX IF NOT EXISTS idx_analysis_run_events_run_seq
                ON analysis_run_events(run_id, seq);
                """
            )
            self._ensure_columns(
                conn,
                "analysis_reports",
                {
                    "run_id": "TEXT",
                    "stock_name": "TEXT NOT NULL DEFAULT ''",
                    "owner_key": "TEXT NOT NULL DEFAULT ''",
                    "owner_uid": "TEXT NOT NULL DEFAULT ''",
                    "owner_email": "TEXT NOT NULL DEFAULT ''",
                },
            )
            conn.execute(
                """
                UPDATE analysis_reports
                SET owner_key = CASE
                        WHEN instr(owner, '@') > 1
                            THEN 'email:' || lower(trim(owner))
                        ELSE 'uid:' || trim(owner)
                    END,
                    owner_email = CASE
                        WHEN instr(owner, '@') > 1 THEN lower(trim(owner))
                        ELSE owner_email
                    END,
                    owner_uid = CASE
                        WHEN instr(owner, '@') > 1 THEN owner_uid
                        ELSE trim(owner)
                    END
                WHERE trim(owner_key) = '' AND trim(owner) != ''
                """
            )
            if self._get_setting(conn, "encryption_key") is None:
                self._set_setting(conn, "encryption_key", _b64(secrets.token_bytes(32)))

    def _ensure_columns(
        self,
        conn: sqlite3.Connection,
        table: str,
        columns: dict[str, str],
    ) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def _get_setting(self, conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def _set_setting(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, _now()),
        )
        conn.commit()

    def _key(self) -> bytes:
        with self._connect() as conn:
            value = self._get_setting(conn, "encryption_key")
        if value is None:
            raise RuntimeError("admin encryption key is not initialized")
        return _unb64(value)

    def encrypt_secret(self, plaintext: str) -> tuple[str, str]:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._key()).encrypt(nonce, plaintext.encode("utf-8"), None)
        return _b64(ciphertext), _b64(nonce)

    def decrypt_secret(self, ciphertext: str, nonce: str) -> str:
        data = AESGCM(self._key()).decrypt(_unb64(nonce), _unb64(ciphertext), None)
        return data.decode("utf-8")

    def admin_password_is_configured(self) -> bool:
        with self._connect() as conn:
            return self._get_setting(conn, "admin_password_hash") is not None

    def set_admin_password(self, password: str) -> None:
        if len(password) < 8:
            raise ValueError("admin password must be at least 8 characters")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 210_000)
        with self._connect() as conn:
            self._set_setting(conn, "admin_password_hash", _b64(salt) + ":" + _b64(digest))

    def verify_admin_password(self, password: str) -> bool:
        with self._connect() as conn:
            stored = self._get_setting(conn, "admin_password_hash")
        if not stored:
            return False
        salt_b64, digest_b64 = stored.split(":", 1)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt_b64), 210_000)
        return hmac.compare_digest(digest, _unb64(digest_b64))

    def create_admin_session(self) -> str:
        token = _b64(secrets.token_bytes(32))
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO admin_sessions (token_hash, created_at, expires_at) VALUES (?, ?, ?)",
                (token_hash, _now(), None),
            )
            conn.commit()
        return token

    def verify_admin_session(self, token: str | None) -> bool:
        if not token:
            return False
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT token_hash FROM admin_sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        return row is not None

    def admin_status(self) -> dict[str, Any]:
        return {
            "password_configured": self.admin_password_is_configured(),
            "warning": (
                "API keys are encrypted at rest in the app database. Because the "
                "encryption key is also app-managed, this is suitable for controlled "
                "internal deployments, not as strong as an external secret manager."
            ),
        }

    def list_whitelist(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM access_whitelist ORDER BY email").fetchall()
        return [self._whitelist_row(row) for row in rows]

    def upsert_whitelist(self, payload: dict[str, Any]) -> dict[str, Any]:
        email = str(payload.get("email", "")).strip().lower()
        if not email or "@" not in email:
            raise ValueError("valid email is required")
        now = _now()
        uid = str(payload.get("uid") or "").strip()
        status = str(payload.get("status") or "active").strip()
        if status not in {"active", "blocked", "pending"}:
            raise ValueError("status must be active, blocked, or pending")
        daily_limit = int(payload.get("daily_limit") or 5)
        allowed_models = json.dumps(payload.get("allowed_models") or [], ensure_ascii=False)
        note = str(payload.get("note") or "")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO access_whitelist (
                    email, uid, status, daily_limit, allowed_models, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    uid = excluded.uid,
                    status = excluded.status,
                    daily_limit = excluded.daily_limit,
                    allowed_models = excluded.allowed_models,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (email, uid, status, daily_limit, allowed_models, note, now, now),
            )
            row = conn.execute(
                "SELECT * FROM access_whitelist WHERE email = ?",
                (email,),
            ).fetchone()
            conn.commit()
        return self._whitelist_row(row)

    def delete_whitelist(self, item_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM access_whitelist WHERE id = ?", (item_id,))
            conn.commit()

    def is_identity_allowed(self, email: str | None = None, uid: str | None = None) -> bool:
        with self._connect() as conn:
            protected = self._get_setting(conn, "admin_password_hash") is not None
            if not protected:
                return True
            total = conn.execute("SELECT COUNT(*) AS count FROM access_whitelist").fetchone()["count"]
            if total == 0:
                return False
            row = conn.execute(
                """
                SELECT status FROM access_whitelist
                WHERE (email = ? AND email != '') OR (uid = ? AND uid != '')
                """,
                ((email or "").strip().lower(), (uid or "").strip()),
            ).fetchone()
        return row is not None and row["status"] == "active"

    def _whitelist_row(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["allowed_models"] = json.loads(out.get("allowed_models") or "[]")
        return out

    def list_model_configs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM model_configs ORDER BY is_default DESC, enabled DESC, id DESC"
            ).fetchall()
        return [self._model_row(row) for row in rows]

    def save_model_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider = str(payload.get("provider") or "").strip().lower()
        display_name = str(payload.get("display_name") or provider).strip()
        quick_model = str(payload.get("quick_model") or "").strip()
        deep_model = str(payload.get("deep_model") or "").strip()
        api_key = str(payload.get("api_key") or "")
        if not provider:
            raise ValueError("provider is required")
        if not quick_model or not deep_model:
            raise ValueError("quick_model and deep_model are required")
        if not api_key and not payload.get("id"):
            raise ValueError("api_key is required for new model configs")

        now = _now()
        base_url = str(payload.get("base_url") or "").strip() or None
        enabled = 1 if payload.get("enabled", True) else 0
        is_default = 1 if payload.get("is_default", False) else 0
        item_id = payload.get("id")

        with self._connect() as conn:
            if item_id:
                existing = conn.execute(
                    "SELECT * FROM model_configs WHERE id = ?",
                    (int(item_id),),
                ).fetchone()
                if existing is None:
                    raise ValueError("model config not found")
                if api_key:
                    ciphertext, nonce = self.encrypt_secret(api_key)
                    masked = mask_secret(api_key)
                else:
                    ciphertext = existing["api_key_ciphertext"]
                    nonce = existing["api_key_nonce"]
                    masked = existing["api_key_masked"]
                conn.execute(
                    """
                    UPDATE model_configs SET
                        provider = ?, display_name = ?, base_url = ?, quick_model = ?,
                        deep_model = ?, api_key_ciphertext = ?, api_key_nonce = ?,
                        api_key_masked = ?, enabled = ?, is_default = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        provider, display_name, base_url, quick_model, deep_model,
                        ciphertext, nonce, masked, enabled, is_default, now, int(item_id),
                    ),
                )
            else:
                ciphertext, nonce = self.encrypt_secret(api_key)
                masked = mask_secret(api_key)
                cur = conn.execute(
                    """
                    INSERT INTO model_configs (
                        provider, display_name, base_url, quick_model, deep_model,
                        api_key_ciphertext, api_key_nonce, api_key_masked,
                        enabled, is_default, created_by, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        provider, display_name, base_url, quick_model, deep_model,
                        ciphertext, nonce, masked, enabled, is_default,
                        str(payload.get("created_by") or ""), now, now,
                    ),
                )
                item_id = cur.lastrowid

            if is_default:
                conn.execute(
                    "UPDATE model_configs SET is_default = 0 WHERE id != ?",
                    (int(item_id),),
                )
            row = conn.execute("SELECT * FROM model_configs WHERE id = ?", (int(item_id),)).fetchone()
            conn.commit()
        return self._model_row(row)

    def set_default_model_config(self, item_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM model_configs WHERE id = ? AND enabled = 1",
                (item_id,),
            ).fetchone()
            if row is None:
                raise ValueError("enabled model config not found")
            conn.execute("UPDATE model_configs SET is_default = 0")
            conn.execute(
                "UPDATE model_configs SET is_default = 1, updated_at = ? WHERE id = ?",
                (_now(), item_id),
            )
            conn.commit()

    def delete_model_config(self, item_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM model_configs WHERE id = ?", (item_id,))
            conn.commit()

    def get_default_runtime_model(self) -> RuntimeModelConfig | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM model_configs
                WHERE enabled = 1
                ORDER BY is_default DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return self._runtime_model_row(row)

    def get_runtime_model_config(self, item_id: int) -> RuntimeModelConfig | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM model_configs WHERE id = ?",
                (int(item_id),),
            ).fetchone()
        if row is None:
            return None
        return self._runtime_model_row(row)

    def _runtime_model_row(self, row: sqlite3.Row) -> RuntimeModelConfig:
        return RuntimeModelConfig(
            provider=row["provider"],
            base_url=row["base_url"],
            quick_model=row["quick_model"],
            deep_model=row["deep_model"],
            api_key=self.decrypt_secret(row["api_key_ciphertext"], row["api_key_nonce"]),
        )

    def _model_row(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out.pop("api_key_ciphertext", None)
        out.pop("api_key_nonce", None)
        out["enabled"] = bool(out["enabled"])
        out["is_default"] = bool(out["is_default"])
        return out

    # ------------------------------------------------------------------
    # Durable analysis runs
    # ------------------------------------------------------------------
    def create_analysis_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        owner_key = str(payload.get("owner_key") or "").strip()
        ticker = str(payload.get("ticker") or "").strip()
        trade_date = str(payload.get("trade_date") or "").strip()
        if not owner_key:
            raise ValueError("owner_key is required")
        if not ticker:
            raise ValueError("ticker is required")
        if not trade_date:
            raise ValueError("trade_date is required")

        run_id = str(payload.get("id") or uuid.uuid4())
        now = _now()
        analysts = json.dumps(payload.get("analysts") or [], ensure_ascii=False)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                """
                SELECT * FROM analysis_runs
                WHERE owner_key = ? AND status IN ('queued', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (owner_key,),
            ).fetchone()
            if active is not None:
                raise ActiveRunExists(self._analysis_run_row(active))
            try:
                conn.execute(
                    """
                    INSERT INTO analysis_runs (
                        id, owner_key, owner_uid, owner_email, ticker, stock_name,
                        trade_date, asset_type, analysts, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                    """,
                    (
                        run_id,
                        owner_key,
                        str(payload.get("owner_uid") or "").strip(),
                        str(payload.get("owner_email") or "").strip().lower(),
                        ticker,
                        str(payload.get("stock_name") or "").strip(),
                        trade_date,
                        str(payload.get("asset_type") or "stock").strip(),
                        analysts,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                active = conn.execute(
                    """
                    SELECT * FROM analysis_runs
                    WHERE owner_key = ? AND status IN ('queued', 'running')
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (owner_key,),
                ).fetchone()
                if active is not None:
                    raise ActiveRunExists(self._analysis_run_row(active)) from exc
                raise
            row = conn.execute(
                "SELECT * FROM analysis_runs WHERE id = ?", (run_id,)
            ).fetchone()
            conn.commit()
        return self._analysis_run_row(row)

    def claim_analysis_run(self, run_id: str) -> dict[str, Any] | None:
        now = _now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE analysis_runs
                SET status = 'running', started_at = COALESCE(started_at, ?),
                    heartbeat_at = ?, error_type = '', error_message = ''
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, str(run_id)),
            )
            if cur.rowcount != 1:
                conn.commit()
                return None
            row = conn.execute(
                "SELECT * FROM analysis_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
            conn.commit()
        return self._analysis_run_row(row)

    def touch_analysis_run(self, run_id: str, current_agent: str = "") -> None:
        with self._connect() as conn:
            if current_agent:
                conn.execute(
                    "UPDATE analysis_runs SET heartbeat_at = ?, current_agent = ? WHERE id = ?",
                    (_now(), current_agent, str(run_id)),
                )
            else:
                conn.execute(
                    "UPDATE analysis_runs SET heartbeat_at = ? WHERE id = ?",
                    (_now(), str(run_id)),
                )
            conn.commit()

    def append_analysis_event(self, run_id: str, event: Any) -> dict[str, Any]:
        event_name = str(getattr(event, "event", "") or "")
        event_data = dict(getattr(event, "data", {}) or {})
        if not event_name:
            raise ValueError("event name is required")
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT last_event_seq FROM analysis_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
            if row is None:
                raise ValueError("analysis run not found")
            seq = int(row["last_event_seq"]) + 1
            conn.execute(
                """
                INSERT INTO analysis_run_events (run_id, seq, event, data, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    seq,
                    event_name,
                    json.dumps(event_data, ensure_ascii=False, default=str),
                    now,
                ),
            )
            current_agent = str(event_data.get("agent") or event_data.get("current_agent") or "")
            if current_agent:
                conn.execute(
                    """
                    UPDATE analysis_runs
                    SET last_event_seq = ?, heartbeat_at = ?, current_agent = ?
                    WHERE id = ?
                    """,
                    (seq, now, current_agent, str(run_id)),
                )
            else:
                conn.execute(
                    """
                    UPDATE analysis_runs SET last_event_seq = ?, heartbeat_at = ?
                    WHERE id = ?
                    """,
                    (seq, now, str(run_id)),
                )
            conn.commit()
        return {
            "run_id": str(run_id),
            "seq": seq,
            "event": event_name,
            "data": event_data,
            "created_at": now,
        }

    def list_analysis_events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, seq, event, data, created_at
                FROM analysis_run_events
                WHERE run_id = ? AND seq > ? ORDER BY seq
                """,
                (str(run_id), int(after)),
            ).fetchall()
        return [self._analysis_event_row(row) for row in rows]

    def get_analysis_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_runs WHERE id = ?", (str(run_id),)
            ).fetchone()
        return None if row is None else self._analysis_run_row(row)

    def get_active_analysis_run(self, owner_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM analysis_runs
                WHERE owner_key = ? AND status IN ('queued', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (str(owner_key),),
            ).fetchone()
        return None if row is None else self._analysis_run_row(row)

    def list_queued_analysis_runs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM analysis_runs WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
        return [self._analysis_run_row(row) for row in rows]

    def complete_analysis_run(self, run_id: str, report_id: int | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE analysis_runs
                SET status = 'completed', report_id = ?, finished_at = ?,
                    heartbeat_at = ?, current_agent = ''
                WHERE id = ?
                """,
                (report_id, _now(), _now(), str(run_id)),
            )
            conn.commit()

    def fail_analysis_run(
        self,
        run_id: str,
        error_type: str,
        error_message: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE analysis_runs
                SET status = 'failed', error_type = ?, error_message = ?,
                    finished_at = ?, heartbeat_at = ?, current_agent = ''
                WHERE id = ?
                """,
                (str(error_type), str(error_message), _now(), _now(), str(run_id)),
            )
            conn.commit()

    def mark_interrupted_runs_failed(self) -> int:
        now = _now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE analysis_runs
                SET status = 'failed', error_type = 'WorkerInterrupted',
                    error_message = '分析服务重启，运行中的任务已中断。',
                    finished_at = ?, heartbeat_at = ?, current_agent = ''
                WHERE status = 'running'
                """,
                (now, now),
            )
            conn.commit()
        return int(cur.rowcount)

    def _analysis_run_row(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["analysts"] = json.loads(out.get("analysts") or "[]")
        return out

    def _analysis_event_row(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["data"] = json.loads(out.get("data") or "{}")
        return out

    # ------------------------------------------------------------------
    # Analysis report history
    # ------------------------------------------------------------------
    def save_analysis_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        ticker = str(payload.get("ticker") or "").strip()
        if not ticker:
            raise ValueError("ticker is required")
        trade_date = str(payload.get("trade_date") or "").strip()
        analysts = json.dumps(payload.get("analysts") or [], ensure_ascii=False)
        sections_map = payload.get("sections") or {}
        if not isinstance(sections_map, dict):
            raise ValueError("sections must be a mapping of section -> content")
        # Drop empty sections so the history stays meaningful.
        clean_sections = {
            str(key): str(value)
            for key, value in sections_map.items()
            if value and str(value).strip()
        }
        sections = json.dumps(clean_sections, ensure_ascii=False)
        decision = str(payload.get("decision") or "").strip()
        owner = str(payload.get("owner") or "").strip()
        run_id = str(payload.get("run_id") or "").strip() or None
        stock_name = str(payload.get("stock_name") or "").strip()
        owner_key = str(payload.get("owner_key") or "").strip()
        owner_uid = str(payload.get("owner_uid") or "").strip()
        owner_email = str(payload.get("owner_email") or owner or "").strip().lower()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO analysis_reports (
                    ticker, trade_date, analysts, sections, decision, owner, created_at,
                    run_id, stock_name, owner_key, owner_uid, owner_email
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    trade_date,
                    analysts,
                    sections,
                    decision,
                    owner,
                    _now(),
                    run_id,
                    stock_name,
                    owner_key,
                    owner_uid,
                    owner_email,
                ),
            )
            row = conn.execute(
                "SELECT * FROM analysis_reports WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            conn.commit()
        return self._report_row(row, include_sections=True)

    def list_analysis_reports(
        self,
        limit: int = 100,
        owner_key: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if owner_key is None:
                rows = conn.execute(
                    "SELECT * FROM analysis_reports ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM analysis_reports
                    WHERE owner_key = ? ORDER BY id DESC LIMIT ?
                    """,
                    (str(owner_key), int(limit)),
                ).fetchall()
        return [self._report_row(row, include_sections=False) for row in rows]

    def get_analysis_report(
        self,
        item_id: int,
        owner_key: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            if owner_key is None:
                row = conn.execute(
                    "SELECT * FROM analysis_reports WHERE id = ?", (int(item_id),)
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM analysis_reports WHERE id = ? AND owner_key = ?
                    """,
                    (int(item_id), str(owner_key)),
                ).fetchone()
        if row is None:
            return None
        return self._report_row(row, include_sections=True)

    def delete_analysis_report(self, item_id: int, owner_key: str | None = None) -> bool:
        with self._connect() as conn:
            if owner_key is None:
                cur = conn.execute(
                    "DELETE FROM analysis_reports WHERE id = ?", (int(item_id),)
                )
            else:
                cur = conn.execute(
                    "DELETE FROM analysis_reports WHERE id = ? AND owner_key = ?",
                    (int(item_id), str(owner_key)),
                )
            conn.commit()
        return cur.rowcount == 1

    def _report_row(self, row: sqlite3.Row, include_sections: bool) -> dict[str, Any]:
        out = dict(row)
        out["analysts"] = json.loads(out.get("analysts") or "[]")
        sections = json.loads(out.get("sections") or "{}")
        if include_sections:
            out["sections"] = sections
        else:
            out.pop("sections", None)
            out["section_keys"] = list(sections.keys())
        return out


_STORE: AdminStore | None = None


def get_admin_store() -> AdminStore:
    global _STORE
    if _STORE is None:
        _STORE = AdminStore()
    return _STORE

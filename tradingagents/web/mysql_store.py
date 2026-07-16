"""CloudBase MySQL implementation of the Web application store."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .admin_store import (
    ActiveRunExists,
    RuntimeModelConfig,
    _b64,
    _unb64,
    mask_secret,
)
from .mysql_migrations import MIGRATIONS
from .store import QueueLimitReached, RuntimeSettings, TaskSubmissionPaused

try:
    import pymysql
except ImportError:  # pragma: no cover - exercised by clean installs without the extra
    pymysql = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_mysql_url(database_url: str) -> dict[str, Any]:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ValueError("database URL must use mysql:// or mysql+pymysql://")
    database = parsed.path.lstrip("/")
    if not parsed.hostname or not database:
        raise ValueError("database URL must include host and database name")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": unquote(database),
    }


class MySQLApplicationStore:
    """Persist Web state in a CloudBase-compatible MySQL database."""

    def __init__(self, database_url: str, master_key: bytes):
        if pymysql is None:
            raise RuntimeError(
                "CloudBase MySQL support requires the 'cloudbase' package extra"
            )
        if len(master_key) != 32:
            raise ValueError("master_key must be exactly 32 bytes")
        self.database_url = database_url
        self.master_key = master_key
        self._connection_args = parse_mysql_url(database_url)
        self._apply_migrations()
        self._seed_runtime_settings()

    def _connect(self):
        return pymysql.connect(
            **self._connection_args,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            charset="utf8mb4",
        )

    def _apply_migrations(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INT PRIMARY KEY,
                        applied_at VARCHAR(40) NOT NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                cursor.execute("SELECT version FROM schema_migrations")
                applied = {int(row["version"]) for row in cursor.fetchall()}
                for version, statements in MIGRATIONS:
                    if version in applied:
                        continue
                    for statement in statements:
                        cursor.execute(statement)
                    cursor.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                        (version, _now()),
                    )
            conn.commit()

    def _set_setting(
        self,
        cursor,
        key: str,
        value: str,
        *,
        updated_by: str = "",
        updated_at: str | None = None,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO app_settings (`key`, value, updated_at, updated_by)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                value = VALUES(value),
                updated_at = VALUES(updated_at),
                updated_by = VALUES(updated_by)
            """,
            (key, value, updated_at or _now(), updated_by),
        )

    def _seed_runtime_settings(self) -> None:
        defaults = {
            "analysis_concurrency_limit": "2",
            "analysis_queue_limit": "20",
            "accept_new_tasks": "true",
        }
        with self._connect() as conn:
            with conn.cursor() as cursor:
                for key, value in defaults.items():
                    cursor.execute(
                        """
                        INSERT IGNORE INTO app_settings (`key`, value, updated_at, updated_by)
                        VALUES (%s, %s, %s, '')
                        """,
                        (key, value, _now()),
                    )
            conn.commit()

    def _runtime_settings_from_cursor(self, cursor, *, for_update: bool = False):
        suffix = " FOR UPDATE" if for_update else ""
        cursor.execute(
            """
            SELECT `key`, value, updated_at, updated_by
            FROM app_settings
            WHERE `key` IN (
                'analysis_concurrency_limit',
                'analysis_queue_limit',
                'accept_new_tasks'
            )
            """
            + suffix
        )
        rows = {str(row["key"]): row for row in cursor.fetchall()}
        try:
            accepting_value = str(rows["accept_new_tasks"]["value"]).lower()
            if accepting_value not in {"true", "false"}:
                raise ValueError("invalid accept_new_tasks")
            primary = rows["analysis_concurrency_limit"]
            return RuntimeSettings.from_payload(
                {
                    "analysis_concurrency_limit": int(primary["value"]),
                    "analysis_queue_limit": int(rows["analysis_queue_limit"]["value"]),
                    "accept_new_tasks": accepting_value == "true",
                },
                updated_by=str(primary.get("updated_by") or ""),
                updated_at=str(primary.get("updated_at") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return RuntimeSettings(
                analysis_concurrency_limit=1,
                analysis_queue_limit=20,
                accept_new_tasks=True,
                warning=(
                    "Stored runtime settings were invalid; "
                    "concurrency was reduced to 1."
                ),
            )

    def get_runtime_settings(self) -> RuntimeSettings:
        with self._connect() as conn, conn.cursor() as cursor:
            return self._runtime_settings_from_cursor(cursor)

    def update_runtime_settings(
        self,
        payload: dict[str, Any],
        updated_by: str,
    ) -> RuntimeSettings:
        now = _now()
        settings = RuntimeSettings.from_payload(
            payload,
            updated_by=str(updated_by),
            updated_at=now,
        )
        values = {
            "analysis_concurrency_limit": str(settings.analysis_concurrency_limit),
            "analysis_queue_limit": str(settings.analysis_queue_limit),
            "accept_new_tasks": (
                "true" if settings.accept_new_tasks else "false"
            ),
        }
        with self._connect() as conn:
            with conn.cursor() as cursor:
                for key, value in values.items():
                    self._set_setting(
                        cursor,
                        key,
                        value,
                        updated_by=str(updated_by),
                        updated_at=now,
                    )
            conn.commit()
        return settings

    def encrypt_secret(self, plaintext: str) -> tuple[str, str]:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self.master_key).encrypt(
            nonce,
            plaintext.encode("utf-8"),
            None,
        )
        return _b64(ciphertext), _b64(nonce)

    def decrypt_secret(self, ciphertext: str, nonce: str) -> str:
        data = AESGCM(self.master_key).decrypt(
            _unb64(nonce),
            _unb64(ciphertext),
            None,
        )
        return data.decode("utf-8")

    def _get_setting(self, cursor, key: str) -> str | None:
        cursor.execute(
            "SELECT value FROM app_settings WHERE `key` = %s",
            (key,),
        )
        row = cursor.fetchone()
        return None if row is None else str(row["value"])

    def admin_password_is_configured(self) -> bool:
        with self._connect() as conn, conn.cursor() as cursor:
            return self._get_setting(cursor, "admin_password_hash") is not None

    def set_admin_password(self, password: str) -> None:
        if len(password) < 8:
            raise ValueError("admin password must be at least 8 characters")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            210_000,
        )
        with self._connect() as conn:
            with conn.cursor() as cursor:
                self._set_setting(
                    cursor,
                    "admin_password_hash",
                    _b64(salt) + ":" + _b64(digest),
                )
            conn.commit()

    def verify_admin_password(self, password: str) -> bool:
        with self._connect() as conn, conn.cursor() as cursor:
            stored = self._get_setting(cursor, "admin_password_hash")
        if not stored:
            return False
        salt_b64, digest_b64 = stored.split(":", 1)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _unb64(salt_b64),
            210_000,
        )
        return hmac.compare_digest(digest, _unb64(digest_b64))

    def create_admin_session(self) -> str:
        token = _b64(secrets.token_bytes(32))
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO admin_sessions (token_hash, created_at, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (token_hash, _now(), None),
                )
            conn.commit()
        return token

    def verify_admin_session(self, token: str | None) -> bool:
        if not token:
            return False
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT token_hash FROM admin_sessions WHERE token_hash = %s",
                (token_hash,),
            )
            return cursor.fetchone() is not None

    def admin_status(self) -> dict[str, Any]:
        return {
            "password_configured": self.admin_password_is_configured(),
            "warning": (
                "API keys are encrypted with the CloudBase service master key."
            ),
        }

    def list_whitelist(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute("SELECT * FROM access_whitelist ORDER BY email")
            rows = cursor.fetchall()
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
        allowed_models = json.dumps(
            payload.get("allowed_models") or [],
            ensure_ascii=False,
        )
        note = str(payload.get("note") or "")
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO access_whitelist (
                        email, uid, status, daily_limit, allowed_models, note,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        uid = VALUES(uid),
                        status = VALUES(status),
                        daily_limit = VALUES(daily_limit),
                        allowed_models = VALUES(allowed_models),
                        note = VALUES(note),
                        updated_at = VALUES(updated_at)
                    """,
                    (
                        email,
                        uid,
                        status,
                        daily_limit,
                        allowed_models,
                        note,
                        now,
                        now,
                    ),
                )
                cursor.execute(
                    "SELECT * FROM access_whitelist WHERE email = %s",
                    (email,),
                )
                row = cursor.fetchone()
            conn.commit()
        return self._whitelist_row(row)

    def delete_whitelist(self, item_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM access_whitelist WHERE id = %s",
                    (int(item_id),),
                )
            conn.commit()

    def is_identity_allowed(
        self,
        email: str | None = None,
        uid: str | None = None,
    ) -> bool:
        with self._connect() as conn, conn.cursor() as cursor:
            protected = (
                self._get_setting(cursor, "admin_password_hash") is not None
            )
            if not protected:
                return True
            cursor.execute("SELECT COUNT(*) AS count FROM access_whitelist")
            if int(cursor.fetchone()["count"]) == 0:
                return False
            cursor.execute(
                """
                    SELECT status FROM access_whitelist
                    WHERE (email = %s AND email != '')
                       OR (uid = %s AND uid != '')
                    LIMIT 1
                    """,
                ((email or "").strip().lower(), (uid or "").strip()),
            )
            row = cursor.fetchone()
        return row is not None and row["status"] == "active"

    def _whitelist_row(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out["allowed_models"] = json.loads(out.get("allowed_models") or "[]")
        return out

    def get_app_user(self, uid: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM app_users WHERE uid = %s",
                (str(uid).strip(),),
            )
            row = cursor.fetchone()
        return None if row is None else dict(row)

    def list_app_users(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM app_users ORDER BY role, email, uid"
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def upsert_app_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        uid = str(payload.get("uid") or "").strip()
        if not uid:
            raise ValueError("uid is required")
        role = str(payload.get("role") or "user").strip().lower()
        if role not in {"admin", "user"}:
            raise ValueError("role must be admin or user")
        status = str(payload.get("status") or "active").strip().lower()
        if status not in {"active", "disabled"}:
            raise ValueError("status must be active or disabled")
        daily_limit = int(payload.get("daily_limit", 5))
        if daily_limit < 0:
            raise ValueError("daily_limit must be non-negative")
        email = str(payload.get("email") or "").strip().lower()
        display_name = str(payload.get("display_name") or "").strip()
        now = _now()
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO app_users (
                    uid, email, display_name, role, status, daily_limit,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    email = VALUES(email),
                    display_name = VALUES(display_name),
                    role = VALUES(role),
                    status = VALUES(status),
                    daily_limit = VALUES(daily_limit),
                    updated_at = VALUES(updated_at)
                """,
                (
                    uid,
                    email,
                    display_name,
                    role,
                    status,
                    daily_limit,
                    now,
                    now,
                ),
            )
            cursor.execute(
                "SELECT * FROM app_users WHERE uid = %s",
                (uid,),
            )
            row = cursor.fetchone()
            conn.commit()
        return dict(row)

    def delete_app_user(self, uid: str) -> bool:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM app_users WHERE uid = %s",
                (str(uid).strip(),),
            )
            deleted = cursor.rowcount == 1
            conn.commit()
        return deleted

    def list_model_configs(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                    SELECT * FROM model_configs
                    ORDER BY is_default DESC, enabled DESC, id DESC
                    """
            )
            rows = cursor.fetchall()
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
            with conn.cursor() as cursor:
                if item_id:
                    cursor.execute(
                        "SELECT * FROM model_configs WHERE id = %s FOR UPDATE",
                        (int(item_id),),
                    )
                    existing = cursor.fetchone()
                    if existing is None:
                        raise ValueError("model config not found")
                    if api_key:
                        ciphertext, nonce = self.encrypt_secret(api_key)
                        masked = mask_secret(api_key)
                    else:
                        ciphertext = existing["api_key_ciphertext"]
                        nonce = existing["api_key_nonce"]
                        masked = existing["api_key_masked"]
                    cursor.execute(
                        """
                        UPDATE model_configs SET
                            provider = %s, display_name = %s, base_url = %s,
                            quick_model = %s, deep_model = %s,
                            api_key_ciphertext = %s, api_key_nonce = %s,
                            api_key_masked = %s, enabled = %s, is_default = %s,
                            updated_at = %s
                        WHERE id = %s
                        """,
                        (
                            provider,
                            display_name,
                            base_url,
                            quick_model,
                            deep_model,
                            ciphertext,
                            nonce,
                            masked,
                            enabled,
                            is_default,
                            now,
                            int(item_id),
                        ),
                    )
                else:
                    ciphertext, nonce = self.encrypt_secret(api_key)
                    masked = mask_secret(api_key)
                    cursor.execute(
                        """
                        INSERT INTO model_configs (
                            provider, display_name, base_url, quick_model,
                            deep_model, api_key_ciphertext, api_key_nonce,
                            api_key_masked, enabled, is_default, created_by,
                            created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            provider,
                            display_name,
                            base_url,
                            quick_model,
                            deep_model,
                            ciphertext,
                            nonce,
                            masked,
                            enabled,
                            is_default,
                            str(payload.get("created_by") or ""),
                            now,
                            now,
                        ),
                    )
                    item_id = cursor.lastrowid
                if is_default:
                    cursor.execute(
                        "UPDATE model_configs SET is_default = 0 WHERE id != %s",
                        (int(item_id),),
                    )
                cursor.execute(
                    "SELECT * FROM model_configs WHERE id = %s",
                    (int(item_id),),
                )
                row = cursor.fetchone()
            conn.commit()
        return self._model_row(row)

    def set_default_model_config(self, item_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id FROM model_configs
                    WHERE id = %s AND enabled = 1
                    """,
                    (int(item_id),),
                )
                if cursor.fetchone() is None:
                    raise ValueError("enabled model config not found")
                cursor.execute("UPDATE model_configs SET is_default = 0")
                cursor.execute(
                    """
                    UPDATE model_configs
                    SET is_default = 1, updated_at = %s
                    WHERE id = %s
                    """,
                    (_now(), int(item_id)),
                )
            conn.commit()

    def delete_model_config(self, item_id: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM model_configs WHERE id = %s",
                    (int(item_id),),
                )
            conn.commit()

    def get_default_runtime_model(self) -> RuntimeModelConfig | None:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                    SELECT * FROM model_configs
                    WHERE enabled = 1
                    ORDER BY is_default DESC, id DESC
                    LIMIT 1
                    """
            )
            row = cursor.fetchone()
        return None if row is None else self._runtime_model_row(row)

    def get_runtime_model_config(
        self,
        item_id: int,
    ) -> RuntimeModelConfig | None:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM model_configs WHERE id = %s",
                (int(item_id),),
            )
            row = cursor.fetchone()
        return None if row is None else self._runtime_model_row(row)

    def _runtime_model_row(self, row: dict[str, Any]) -> RuntimeModelConfig:
        return RuntimeModelConfig(
            provider=str(row["provider"]),
            base_url=row.get("base_url"),
            quick_model=str(row["quick_model"]),
            deep_model=str(row["deep_model"]),
            api_key=self.decrypt_secret(
                str(row["api_key_ciphertext"]),
                str(row["api_key_nonce"]),
            ),
        )

    def _model_row(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out.pop("api_key_ciphertext", None)
        out.pop("api_key_nonce", None)
        out["enabled"] = bool(out["enabled"])
        out["is_default"] = bool(out["is_default"])
        return out

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
            try:
                with conn.cursor() as cursor:
                    settings = self._runtime_settings_from_cursor(
                        cursor,
                        for_update=True,
                    )
                    if not settings.accept_new_tasks:
                        raise TaskSubmissionPaused(
                            "new analysis submissions are paused"
                        )
                    cursor.execute(
                        """
                        SELECT COUNT(*) AS count FROM analysis_runs
                        WHERE status = 'queued'
                        """
                    )
                    if int(cursor.fetchone()["count"]) >= settings.analysis_queue_limit:
                        raise QueueLimitReached("analysis queue limit reached")
                    cursor.execute(
                        """
                        INSERT INTO active_analysis_owners (
                            owner_key, run_id, acquired_at
                        ) VALUES (%s, %s, %s)
                        """,
                        (owner_key, run_id, now),
                    )
                    cursor.execute(
                        """
                        INSERT INTO analysis_runs (
                            id, owner_key, owner_uid, owner_email, ticker,
                            stock_name, trade_date, asset_type, analysts,
                            status, created_at, error_message
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, 'queued', %s, ''
                        )
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
                    cursor.execute(
                        "SELECT * FROM analysis_runs WHERE id = %s",
                        (run_id,),
                    )
                    row = cursor.fetchone()
                conn.commit()
            except pymysql.err.IntegrityError as exc:
                conn.rollback()
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT runs.* FROM active_analysis_owners active
                        JOIN analysis_runs runs ON runs.id = active.run_id
                        WHERE active.owner_key = %s
                        """,
                        (owner_key,),
                    )
                    active = cursor.fetchone()
                if active is not None:
                    raise ActiveRunExists(self._analysis_run_row(active)) from exc
                raise
            except Exception:
                conn.rollback()
                raise
        return self._analysis_run_row(row)

    def claim_analysis_run(self, run_id: str) -> dict[str, Any] | None:
        now = _now()
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE analysis_runs
                    SET status = 'running',
                        started_at = COALESCE(started_at, %s),
                        heartbeat_at = %s,
                        error_type = '',
                        error_message = ''
                    WHERE id = %s AND status = 'queued'
                    """,
                    (now, now, str(run_id)),
                )
                if cursor.rowcount != 1:
                    conn.commit()
                    return None
                cursor.execute(
                    "SELECT * FROM analysis_runs WHERE id = %s",
                    (str(run_id),),
                )
                row = cursor.fetchone()
            conn.commit()
        return self._analysis_run_row(row)

    def claim_next_analysis_run(self) -> dict[str, Any] | None:
        now = _now()
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id FROM analysis_runs
                    WHERE status = 'queued'
                    ORDER BY created_at, id
                    LIMIT 1 FOR UPDATE
                    """
                )
                queued = cursor.fetchone()
                if queued is None:
                    conn.commit()
                    return None
                run_id = str(queued["id"])
                cursor.execute(
                    """
                    UPDATE analysis_runs
                    SET status = 'running',
                        started_at = COALESCE(started_at, %s),
                        heartbeat_at = %s,
                        error_type = '',
                        error_message = ''
                    WHERE id = %s AND status = 'queued'
                    """,
                    (now, now, run_id),
                )
                cursor.execute(
                    "SELECT * FROM analysis_runs WHERE id = %s",
                    (run_id,),
                )
                row = cursor.fetchone()
            conn.commit()
        return self._analysis_run_row(row)

    def count_queued_analysis_runs(self) -> int:
        return self._count_runs("queued")

    def count_running_analysis_runs(self) -> int:
        return self._count_runs("running")

    def _count_runs(self, status: str) -> int:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS count FROM analysis_runs WHERE status = %s",
                (status,),
            )
            return int(cursor.fetchone()["count"])

    def touch_analysis_run(self, run_id: str, current_agent: str = "") -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                if current_agent:
                    cursor.execute(
                        """
                        UPDATE analysis_runs
                        SET heartbeat_at = %s, current_agent = %s
                        WHERE id = %s
                        """,
                        (_now(), current_agent, str(run_id)),
                    )
                else:
                    cursor.execute(
                        "UPDATE analysis_runs SET heartbeat_at = %s WHERE id = %s",
                        (_now(), str(run_id)),
                    )
            conn.commit()

    def append_analysis_event(
        self,
        run_id: str,
        event: Any,
    ) -> dict[str, Any]:
        event_name = str(getattr(event, "event", "") or "")
        event_data = dict(getattr(event, "data", {}) or {})
        if not event_name:
            raise ValueError("event name is required")
        now = _now()
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT last_event_seq FROM analysis_runs
                    WHERE id = %s FOR UPDATE
                    """,
                    (str(run_id),),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("analysis run not found")
                seq = int(row["last_event_seq"]) + 1
                cursor.execute(
                    """
                    INSERT INTO analysis_run_events (
                        run_id, seq, event, data, created_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        str(run_id),
                        seq,
                        event_name,
                        json.dumps(event_data, ensure_ascii=False, default=str),
                        now,
                    ),
                )
                current_agent = str(
                    event_data.get("agent")
                    or event_data.get("current_agent")
                    or ""
                )
                cursor.execute(
                    """
                    UPDATE analysis_runs
                    SET last_event_seq = %s, heartbeat_at = %s,
                        current_agent = %s
                    WHERE id = %s
                    """,
                    (seq, now, current_agent, str(run_id)),
                )
            conn.commit()
        return {
            "run_id": str(run_id),
            "seq": seq,
            "event": event_name,
            "data": event_data,
            "created_at": now,
        }

    def list_analysis_events(
        self,
        run_id: str,
        after: int = 0,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                    SELECT run_id, seq, event, data, created_at
                    FROM analysis_run_events
                    WHERE run_id = %s AND seq > %s
                    ORDER BY seq
                    """,
                (str(run_id), int(after)),
            )
            rows = cursor.fetchall()
        return [self._analysis_event_row(row) for row in rows]

    def get_analysis_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM analysis_runs WHERE id = %s",
                (str(run_id),),
            )
            row = cursor.fetchone()
        return None if row is None else self._analysis_run_row(row)

    def get_active_analysis_run(
        self,
        owner_key: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                    SELECT * FROM analysis_runs
                    WHERE owner_key = %s AND status IN ('queued', 'running')
                    ORDER BY created_at DESC LIMIT 1
                    """,
                (str(owner_key),),
            )
            row = cursor.fetchone()
        return None if row is None else self._analysis_run_row(row)

    def list_queued_analysis_runs(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                    SELECT * FROM analysis_runs
                    WHERE status = 'queued'
                    ORDER BY created_at, id
                    """
            )
            rows = cursor.fetchall()
        return [self._analysis_run_row(row) for row in rows]

    def complete_analysis_run(
        self,
        run_id: str,
        report_id: int | None = None,
    ) -> None:
        now = _now()
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE analysis_runs
                    SET status = 'completed', report_id = %s, finished_at = %s,
                        heartbeat_at = %s, current_agent = ''
                    WHERE id = %s
                    """,
                    (report_id, now, now, str(run_id)),
                )
                cursor.execute(
                    "DELETE FROM active_analysis_owners WHERE run_id = %s",
                    (str(run_id),),
                )
            conn.commit()

    def fail_analysis_run(
        self,
        run_id: str,
        error_type: str,
        error_message: str,
    ) -> None:
        now = _now()
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE analysis_runs
                    SET status = 'failed', error_type = %s, error_message = %s,
                        finished_at = %s, heartbeat_at = %s, current_agent = ''
                    WHERE id = %s
                    """,
                    (
                        str(error_type),
                        str(error_message),
                        now,
                        now,
                        str(run_id),
                    ),
                )
                cursor.execute(
                    "DELETE FROM active_analysis_owners WHERE run_id = %s",
                    (str(run_id),),
                )
            conn.commit()

    def mark_interrupted_runs_failed(self) -> int:
        now = _now()
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE analysis_runs
                    SET status = 'failed',
                        error_type = 'WorkerInterrupted',
                        error_message = '分析服务重启，运行中的任务已中断。',
                        finished_at = %s,
                        heartbeat_at = %s,
                        current_agent = ''
                    WHERE status = 'running'
                    """,
                    (now, now),
                )
                count = int(cursor.rowcount)
                cursor.execute(
                    """
                    DELETE active FROM active_analysis_owners active
                    JOIN analysis_runs runs ON runs.id = active.run_id
                    WHERE runs.status = 'failed'
                    """
                )
            conn.commit()
        return count

    def _analysis_run_row(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out["analysts"] = json.loads(out.get("analysts") or "[]")
        return out

    def _analysis_event_row(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out["data"] = json.loads(out.get("data") or "{}")
        return out

    def save_analysis_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        ticker = str(payload.get("ticker") or "").strip()
        if not ticker:
            raise ValueError("ticker is required")
        sections_map = payload.get("sections") or {}
        if not isinstance(sections_map, dict):
            raise ValueError("sections must be a mapping of section -> content")
        clean_sections = {
            str(key): str(value)
            for key, value in sections_map.items()
            if value and str(value).strip()
        }
        values = (
            ticker,
            str(payload.get("trade_date") or "").strip(),
            json.dumps(payload.get("analysts") or [], ensure_ascii=False),
            json.dumps(clean_sections, ensure_ascii=False),
            str(payload.get("decision") or "").strip(),
            str(payload.get("owner") or "").strip(),
            _now(),
            str(payload.get("run_id") or "").strip() or None,
            str(payload.get("stock_name") or "").strip(),
            str(payload.get("owner_key") or "").strip(),
            str(payload.get("owner_uid") or "").strip(),
            str(
                payload.get("owner_email")
                or payload.get("owner")
                or ""
            ).strip().lower(),
        )
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO analysis_reports (
                        ticker, trade_date, analysts, sections, decision, owner,
                        created_at, run_id, stock_name, owner_key, owner_uid,
                        owner_email
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    """,
                    values,
                )
                item_id = int(cursor.lastrowid)
                cursor.execute(
                    "SELECT * FROM analysis_reports WHERE id = %s",
                    (item_id,),
                )
                row = cursor.fetchone()
            conn.commit()
        return self._report_row(row, include_sections=True)

    def list_analysis_reports(
        self,
        limit: int = 100,
        owner_key: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cursor:
            if owner_key is None:
                cursor.execute(
                    """
                        SELECT * FROM analysis_reports
                        ORDER BY id DESC LIMIT %s
                        """,
                    (int(limit),),
                )
            else:
                cursor.execute(
                    """
                        SELECT * FROM analysis_reports
                        WHERE owner_key = %s
                        ORDER BY id DESC LIMIT %s
                        """,
                    (str(owner_key), int(limit)),
                )
            rows = cursor.fetchall()
        return [self._report_row(row, include_sections=False) for row in rows]

    def get_analysis_report(
        self,
        item_id: int,
        owner_key: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cursor:
            if owner_key is None:
                cursor.execute(
                    "SELECT * FROM analysis_reports WHERE id = %s",
                    (int(item_id),),
                )
            else:
                cursor.execute(
                    """
                        SELECT * FROM analysis_reports
                        WHERE id = %s AND owner_key = %s
                        """,
                    (int(item_id), str(owner_key)),
                )
            row = cursor.fetchone()
        return None if row is None else self._report_row(row, include_sections=True)

    def delete_analysis_report(
        self,
        item_id: int,
        owner_key: str | None = None,
    ) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                if owner_key is None:
                    cursor.execute(
                        "DELETE FROM analysis_reports WHERE id = %s",
                        (int(item_id),),
                    )
                else:
                    cursor.execute(
                        """
                        DELETE FROM analysis_reports
                        WHERE id = %s AND owner_key = %s
                        """,
                        (int(item_id), str(owner_key)),
                    )
                deleted = cursor.rowcount == 1
            conn.commit()
        return deleted

    def _report_row(
        self,
        row: dict[str, Any],
        include_sections: bool,
    ) -> dict[str, Any]:
        out = dict(row)
        out["analysts"] = json.loads(out.get("analysts") or "[]")
        sections = json.loads(out.get("sections") or "{}")
        if include_sections:
            out["sections"] = sections
        else:
            out.pop("sections", None)
            out["section_keys"] = list(sections.keys())
        return out

    def ping(self) -> bool:
        try:
            with self._connect() as conn, conn.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                return int(cursor.fetchone()["ok"]) == 1
        except Exception:
            return False

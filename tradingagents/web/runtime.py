"""Runtime selection and deployment configuration for the Web application."""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


class RuntimeConfigurationError(ValueError):
    """Raised when the selected Web runtime is incomplete or invalid."""


@dataclass(frozen=True)
class WebRuntimeConfig:
    mode: Literal["local", "cloudbase"]
    database_url: str
    cloudbase_env_id: str = ""
    cloudbase_region: str = "ap-shanghai"
    master_key: bytes | None = None
    database_backend: str = "mysql"
    cloudbase_api_key: str = field(default="", repr=False)


def _required(env: Mapping[str, str], name: str, missing: list[str]) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        missing.append(name)
    return value


def load_web_runtime_config(
    environ: Mapping[str, str] | None = None,
) -> WebRuntimeConfig:
    """Load explicit local or CloudBase runtime configuration."""
    env = os.environ if environ is None else environ
    mode = str(env.get("TRADINGAGENTS_RUNTIME") or "local").strip().lower()
    if mode == "local":
        db_path = Path(".tradingagents") / "web_admin.sqlite3"
        return WebRuntimeConfig(
            mode="local",
            database_url=f"sqlite:///{db_path.as_posix()}",
        )
    if mode != "cloudbase":
        raise RuntimeConfigurationError(
            "TRADINGAGENTS_RUNTIME must be 'local' or 'cloudbase'"
        )

    missing: list[str] = []
    backend = str(env.get("TRADINGAGENTS_DATABASE_BACKEND") or "mysql").strip().lower()
    if backend not in {"mysql", "document"}:
        raise RuntimeConfigurationError("database backend must be mysql or document")
    database_url = (
        _required(env, "TRADINGAGENTS_DATABASE_URL", missing)
        if backend == "mysql" else ""
    )
    api_key = (
        _required(env, "TRADINGAGENTS_CLOUDBASE_API_KEY", missing)
        if backend == "document" else ""
    )
    env_id = _required(env, "TRADINGAGENTS_CLOUDBASE_ENV_ID", missing)
    encoded_key = _required(env, "TRADINGAGENTS_MASTER_KEY", missing)
    if missing:
        raise RuntimeConfigurationError(
            "CloudBase runtime is missing: " + ", ".join(missing)
        )

    try:
        master_key = base64.b64decode(
            encoded_key.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise RuntimeConfigurationError(
            "TRADINGAGENTS_MASTER_KEY must be URL-safe base64"
        ) from exc
    if len(master_key) != 32:
        raise RuntimeConfigurationError(
            "TRADINGAGENTS_MASTER_KEY must decode to exactly 32 bytes"
        )

    return WebRuntimeConfig(
        mode="cloudbase",
        database_url=database_url,
        cloudbase_env_id=env_id,
        cloudbase_region=str(
            env.get("TRADINGAGENTS_CLOUDBASE_REGION") or "ap-shanghai"
        ).strip(),
        master_key=master_key,
        database_backend=backend,
        cloudbase_api_key=api_key,
    )

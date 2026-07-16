"""Runtime selection and deployment configuration for the Web application."""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping
from dataclasses import dataclass
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
    cloudbase_publishable_key: str = ""
    master_key: bytes | None = None


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
    database_url = _required(env, "TRADINGAGENTS_DATABASE_URL", missing)
    env_id = _required(env, "TRADINGAGENTS_CLOUDBASE_ENV_ID", missing)
    publishable_key = _required(
        env,
        "TRADINGAGENTS_CLOUDBASE_PUBLISHABLE_KEY",
        missing,
    )
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
        cloudbase_publishable_key=publishable_key,
        master_key=master_key,
    )

"""Runtime-selected application store construction."""

from __future__ import annotations

from .admin_store import AdminStore
from .mysql_store import MySQLApplicationStore
from .runtime import WebRuntimeConfig, load_web_runtime_config
from .store import ApplicationStore

_STORE: ApplicationStore | None = None


def create_application_store(config: WebRuntimeConfig) -> ApplicationStore:
    if config.mode == "local":
        return AdminStore.from_database_url(config.database_url)
    if config.master_key is None:
        raise ValueError("CloudBase runtime requires a master key")
    return MySQLApplicationStore(config.database_url, config.master_key)


def get_application_store() -> ApplicationStore:
    global _STORE
    if _STORE is None:
        _STORE = create_application_store(load_web_runtime_config())
    return _STORE

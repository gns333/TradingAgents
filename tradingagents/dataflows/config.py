from contextvars import ContextVar
from copy import deepcopy

import tradingagents.default_config as default_config

# Process-wide baseline plus a task-local override. ContextVar values are copied
# into LangGraph/LangChain context-aware executors, so concurrent web runs do
# not overwrite each other's market profile or vendor routing.
_config: dict | None = None
_config_context: ContextVar[dict | None] = ContextVar(
    "tradingagents_dataflow_config",
    default=None,
)


def initialize_config():
    """Initialize the configuration with default values."""
    global _config
    if _config is None:
        _config = deepcopy(default_config.DEFAULT_CONFIG)


def set_config(config: dict):
    """Update the configuration with custom values.

    Dict-valued keys (e.g. ``data_vendors``) are merged one level deep so a
    partial update like ``{"data_vendors": {"core_stock_apis": "alpha_vantage"}}``
    keeps the other nested keys from the default; scalar keys are replaced.
    """
    initialize_config()
    current = _config_context.get()
    resolved = deepcopy(current if current is not None else _config)
    incoming = deepcopy(config)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(resolved.get(key), dict):
            resolved[key].update(value)
        else:
            resolved[key] = value
    _config_context.set(resolved)


def replace_config(config: dict) -> None:
    """Replace the active task's configuration without merging stale keys."""
    initialize_config()
    _config_context.set(deepcopy(config))


def reset_config(config: dict | None = None) -> None:
    """Reset both the process baseline and current context (primarily for tests)."""
    global _config
    _config = deepcopy(config if config is not None else default_config.DEFAULT_CONFIG)
    _config_context.set(deepcopy(_config))


def get_config() -> dict:
    """Get the current configuration."""
    if _config is None:
        initialize_config()
    current = _config_context.get()
    return deepcopy(current if current is not None else _config)


# Initialize with default config
initialize_config()

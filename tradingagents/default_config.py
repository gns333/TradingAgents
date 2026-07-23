import os
from copy import deepcopy

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")
_MARKET_PROFILE_ENV = "TRADINGAGENTS_MARKET_PROFILE"

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {
    "TRADINGAGENTS_LLM_PROVIDER": "llm_provider",
    "TRADINGAGENTS_DEEP_THINK_LLM": "deep_think_llm",
    "TRADINGAGENTS_QUICK_THINK_LLM": "quick_think_llm",
    "TRADINGAGENTS_LLM_BACKEND_URL": "backend_url",
    "TRADINGAGENTS_OUTPUT_LANGUAGE": "output_language",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS": "max_debate_rounds",
    "TRADINGAGENTS_MAX_RISK_ROUNDS": "max_risk_discuss_rounds",
    "TRADINGAGENTS_CHECKPOINT_ENABLED": "checkpoint_enabled",
    "TRADINGAGENTS_BENCHMARK_TICKER": "benchmark_ticker",
    "TRADINGAGENTS_TEMPERATURE": "temperature",
    # Provider-specific reasoning/thinking knobs (None = each provider's own
    # default). Settable here for non-interactive runs; the CLI also offers an
    # interactive choice, which is skipped when the matching var is set.
    "TRADINGAGENTS_GOOGLE_THINKING_LEVEL": "google_thinking_level",
    "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "openai_reasoning_effort",
    "TRADINGAGENTS_ANTHROPIC_EFFORT": "anthropic_effort",
}

MARKET_PROFILES = {
    "china_mainland": {
        "market_profile": "china_mainland",
        "llm_provider": "deepseek",
        "quick_think_llm": "deepseek-v4-flash",
        "deep_think_llm": "deepseek-v4-pro",
        "output_language": "Chinese",
        "global_news_queries": [
            "A股 市场 政策 监管 资金面",
            "中国 宏观经济 CPI PPI PMI 社融 LPR",
            "央行 证监会 财政部 政策 股市",
            "北向资金 融资融券 板块轮动",
            "人民币 汇率 大宗商品 出口 消费",
        ],
        "data_vendors": {
            "core_stock_apis": "akshare,baostock,yfinance",
            "technical_indicators": "akshare,baostock,yfinance",
            "fundamental_data": "akshare,baostock,yfinance",
            "news_data": "akshare,yfinance",
            "macro_data": "akshare",
            "prediction_markets": "polymarket",
        },
        "benchmark_map": {
            ".SH": "000001.SS",
            ".SS": "000001.SS",
            ".SZ": "399001.SZ",
            ".BJ": "899050.BJ",
            "": "000300.SS",
        },
    },
    "hong_kong": {
        "market_profile": "hong_kong",
        "output_language": "Chinese",
        "data_vendors": {
            "core_stock_apis": "akshare,yfinance",
            "technical_indicators": "akshare,yfinance",
            "fundamental_data": "akshare,yfinance",
            "news_data": "akshare,yfinance",
        },
        "benchmark_map": {
            ".HK": "^HSI",
            "": "^HSI",
        },
    },
}


_BOOL_TRUE = ("true", "1", "yes", "on")
_BOOL_FALSE = ("false", "0", "no", "off")


def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value.

    Invalid values raise ``ValueError`` rather than silently falling back to a
    default — a misspelled boolean (e.g. ``treu``) or non-numeric int should fail
    loudly at startup, not quietly misconfigure an unattended run.
    """
    if isinstance(reference, bool):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(
            f"expected a boolean ({'/'.join(_BOOL_TRUE + _BOOL_FALSE)}), got {value!r}"
        )
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """Apply TRADINGAGENTS_* env vars to the config dict in-place."""
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        try:
            config[key] = _coerce(raw, config.get(key))
        except ValueError as exc:
            raise ValueError(f"Invalid value for {env_var}: {exc}") from exc
    return config


def _deep_merge(base: dict, overlay: dict) -> dict:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _apply_market_profile(config: dict) -> dict:
    profile = os.environ.get(_MARKET_PROFILE_ENV, "").strip()
    if not profile:
        return config
    if profile not in MARKET_PROFILES:
        available = ", ".join(sorted(MARKET_PROFILES))
        raise ValueError(
            f"Invalid value for {_MARKET_PROFILE_ENV}: {profile!r}. "
            f"Available profiles: {available}."
        )
    return _deep_merge(config, MARKET_PROFILES[profile])


_BASE_DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv(
        "TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")
    ),
    "data_cache_dir": os.getenv(
        "TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")
    ),
    "memory_log_path": os.getenv(
        "TRADINGAGENTS_MEMORY_LOG_PATH",
        os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md"),
    ),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "market_profile": "default",
    "llm_provider": "openai",
    "deep_think_llm": "gpt-5.5",
    "quick_think_llm": "gpt-5.4-mini",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,  # "high", "minimal", etc.
    "openai_reasoning_effort": None,  # "medium", "high", "low"
    "anthropic_effort": None,  # "high", "medium", "low"
    # Sampling temperature, forwarded to every provider when set. A low default
    # keeps reports stable while still allowing debate agents some variation;
    # reasoning models may ignore it and output is never bit-identical.
    "temperature": 0.2,
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # Debate and discussion settings
    "max_debate_rounds": 2,
    "max_risk_discuss_rounds": 2,
    "max_recur_limit": 100,
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 20,  # max articles per ticker (ticker-news)
    "global_news_article_limit": 10,  # max articles for global/macro news
    "global_news_lookback_days": 7,  # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "ECB Bank of England BOJ central bank policy",
        "oil commodities supply chain energy",
    ],
    # Data vendor configuration
    # Category-level configuration (default for all tools in category).
    # The configured value is the exact vendor chain — requests are NOT silently
    # routed to vendors you didn't choose. For ordered fallback, list several,
    # e.g. "yfinance,alpha_vantage". "default" uses all available vendors.
    "data_vendors": {
        "core_stock_apis": "yfinance",  # Options: alpha_vantage, yfinance
        "technical_indicators": "yfinance",  # Options: alpha_vantage, yfinance
        "fundamental_data": "yfinance",  # Options: alpha_vantage, yfinance
        "news_data": "yfinance",  # Options: alpha_vantage, yfinance
        "macro_data": "fred",  # Options: fred (needs FRED_API_KEY)
        "prediction_markets": "polymarket",  # Options: polymarket (keyless)
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    "benchmark_ticker": None,
    "benchmark_map": {
        ".NS": "^NSEI",  # NSE India (Nifty 50)
        ".BO": "^BSESN",  # BSE India (Sensex)
        ".T": "^N225",  # Tokyo (Nikkei 225)
        ".HK": "^HSI",  # Hong Kong (Hang Seng)
        ".L": "^FTSE",  # London (FTSE 100)
        ".TO": "^GSPTSE",  # Toronto (TSX Composite)
        ".AX": "^AXJO",  # Australia (ASX 200)
        ".SS": "000001.SS",  # Shanghai (SSE Composite)
        ".SZ": "399001.SZ",  # Shenzhen (SZSE Component)
        "": "SPY",  # default for US-listed tickers (no suffix)
    },
}


DEFAULT_CONFIG = _apply_env_overrides(_apply_market_profile(deepcopy(_BASE_DEFAULT_CONFIG)))

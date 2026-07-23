"""AKShare vendor for Mainland China and Hong Kong market data.

AKShare is intentionally imported lazily so the regular US/global workflow does
not require the optional dependency. Select the vendor with
``data_vendors.* = "akshare"`` or use a supported China market profile.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .china_symbol_utils import ChinaSymbol, parse_china_symbol
from .errors import NoMarketDataError, VendorNotConfiguredError
from .hong_kong_symbol_utils import HongKongSymbol, parse_hong_kong_symbol

_COLUMN_MAP = {
    "日期": "Date",
    "开盘": "Open",
    "最高": "High",
    "最低": "Low",
    "收盘": "Close",
    "成交量": "Volume",
    "成交额": "Amount",
    "换手率": "Turnover",
    "date": "Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
    "amount": "Amount",
    "turnover": "Turnover",
}


class AkshareNotConfiguredError(VendorNotConfiguredError):
    """AKShare was selected but is not installed."""


def _akshare():
    try:
        return importlib.import_module("akshare")
    except ImportError as exc:
        raise AkshareNotConfiguredError(
            "AKShare is required for Mainland China and Hong Kong data. Install it with "
            "`pip install 'tradingagents[china]'` or `pip install akshare`."
        ) from exc


def _parse_symbol(symbol: str) -> ChinaSymbol | HongKongSymbol:
    parsed = parse_china_symbol(symbol) or parse_hong_kong_symbol(symbol)
    if parsed is None:
        raise NoMarketDataError(
            symbol,
            symbol,
            "not a Mainland China A-share or Hong Kong stock symbol",
        )
    return parsed


def _is_hong_kong(symbol: ChinaSymbol | HongKongSymbol) -> bool:
    return isinstance(symbol, HongKongSymbol)


def _market_label(symbol: ChinaSymbol | HongKongSymbol) -> str:
    return "Hong Kong" if _is_hong_kong(symbol) else "A-share"


def _compact_date(date_text: str) -> str:
    return datetime.strptime(date_text, "%Y-%m-%d").strftime("%Y%m%d")


def _empty(data: Any) -> bool:
    return data is None or getattr(data, "empty", False)


def _filter_by_date(
    df: pd.DataFrame, date_column: str, start_date: str, end_date: str
) -> pd.DataFrame:
    if date_column not in df.columns:
        return df
    out = df.copy()
    out[date_column] = pd.to_datetime(out[date_column], errors="coerce")
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    out = out[(out[date_column] >= start) & (out[date_column] <= end)]
    out[date_column] = out[date_column].dt.strftime("%Y-%m-%d")
    return out


def _to_markdownish_csv(df: pd.DataFrame) -> str:
    return df.to_csv(index=False)


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.rename(columns={k: v for k, v in _COLUMN_MAP.items() if k in df.columns})
    wanted = ["Date", "Open", "High", "Low", "Close", "Volume", "Amount", "Turnover"]
    present = [col for col in wanted if col in out.columns]
    out = out[present].copy()
    if "Date" in out.columns:
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in [c for c in present if c != "Date"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["Date", "Close"]) if {"Date", "Close"} <= set(out.columns) else out


def _stock_history(
    symbol: ChinaSymbol | HongKongSymbol,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> pd.DataFrame:
    ak = _akshare()
    compact_start = _compact_date(start_date)
    compact_end = _compact_date(end_date)
    # Do not fall back to AKShare's Sina endpoint here: it initializes
    # py_mini_racer, which can terminate the web process on current macOS Python.
    endpoint = ak.stock_hk_hist if _is_hong_kong(symbol) else ak.stock_zh_a_hist
    source = "stock_hk_hist" if _is_hong_kong(symbol) else "stock_zh_a_hist"
    df = endpoint(
        symbol=symbol.akshare_code,
        period="daily",
        start_date=compact_start,
        end_date=compact_end,
        adjust=adjust,
    )
    if df is not None:
        df.attrs["akshare_source"] = source
    return df


def get_stock_frame(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Return normalized A-share or Hong Kong OHLCV rows as a DataFrame."""
    parsed = _parse_symbol(symbol)
    raw = _stock_history(parsed, start_date, end_date)
    if _empty(raw):
        raise NoMarketDataError(
            symbol, parsed.canonical, f"no rows between {start_date} and {end_date}"
        )

    data = _normalize_ohlcv(raw)
    if data.empty:
        raise NoMarketDataError(
            symbol, parsed.canonical, "AKShare returned no usable OHLCV columns"
        )
    data.attrs["akshare_source"] = raw.attrs.get("akshare_source", "AKShare history")
    return data


def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    """Return A-share or Hong Kong OHLCV history from AKShare."""
    parsed = _parse_symbol(symbol)
    data = get_stock_frame(symbol, start_date, end_date)

    header = (
        f"# {_market_label(parsed)} stock data for {parsed.canonical} from {start_date} to {end_date}\n"
        f"# Source: AKShare {data.attrs.get('akshare_source', 'history')}\n"
        f"# Total records: {len(data)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + _to_markdownish_csv(data)


def get_indicator(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    """Compute common technical indicators from AKShare OHLCV history."""
    parsed = _parse_symbol(symbol)
    start = (
        datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=max(look_back_days, 260))
    ).strftime("%Y-%m-%d")
    raw = _stock_history(parsed, start, curr_date)
    data = _normalize_ohlcv(raw)
    if data.empty:
        raise NoMarketDataError(symbol, parsed.canonical, "no OHLCV rows for indicator calculation")

    close = data["Close"]
    result = pd.DataFrame({"Date": data["Date"]})
    key = indicator.lower()
    if key.endswith("_sma"):
        window = int(key.split("_")[1])
        result[indicator] = close.rolling(window).mean()
    elif key.endswith("_ema"):
        window = int(key.split("_")[1])
        result[indicator] = close.ewm(span=window, adjust=False).mean()
    elif key == "rsi":
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss
        result[indicator] = 100 - (100 / (1 + rs))
    elif key in {"boll", "boll_ub", "boll_lb"}:
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        result[indicator] = {"boll": mid, "boll_ub": mid + 2 * std, "boll_lb": mid - 2 * std}[key]
    else:
        raise ValueError(
            f"Indicator {indicator} is not supported by akshare. "
            "Use close_10_ema, close_50_sma, close_200_sma, rsi, boll, boll_ub, or boll_lb."
        )

    recent = result.tail(max(1, int(look_back_days))).copy()
    recent[indicator] = recent[indicator].round(4)
    return (
        f"## {indicator} values for {parsed.canonical} through {curr_date}\n\n"
        + _to_markdownish_csv(recent)
    )


def _format_frame(title: str, source: str, df: pd.DataFrame, limit: int = 30) -> str:
    if _empty(df):
        return f"DATA_UNAVAILABLE: {title} returned no rows from {source}."
    return f"# {title}\n# Source: AKShare {source}\n\n" + _to_markdownish_csv(df.head(limit))


def _get_hong_kong_fundamentals(
    parsed: HongKongSymbol,
    ak: Any,
) -> str:
    sections = []
    errors = []
    endpoints = (
        ("Security profile", "stock_hk_security_profile_em"),
        ("Company profile", "stock_hk_company_profile_em"),
        ("Latest financial indicators", "stock_hk_financial_indicator_em"),
    )
    for title, source in endpoints:
        fn = getattr(ak, source, None)
        if fn is None:
            errors.append(f"{source}: endpoint unavailable")
            continue
        try:
            df = fn(symbol=parsed.akshare_code)
        except Exception as exc:  # noqa: BLE001 - preserve partial HK fundamentals
            errors.append(f"{source}: {type(exc).__name__}: {exc}")
            continue
        if not _empty(df):
            sections.append(
                _format_frame(
                    f"Hong Kong {title.lower()} for {parsed.canonical}",
                    source,
                    df,
                    limit=50,
                )
            )

    if not sections:
        detail = "; ".join(errors) or "all Hong Kong fundamentals endpoints returned no rows"
        raise NoMarketDataError(parsed.canonical, parsed.canonical, detail)
    if errors:
        sections.append(
            "# Unavailable AKShare sections\n\n" + "\n".join(f"- {item}" for item in errors)
        )
    return "\n\n".join(sections)


def get_fundamentals(ticker: str, curr_date: str | None = None) -> str:
    parsed = _parse_symbol(ticker)
    ak = _akshare()
    if _is_hong_kong(parsed):
        return _get_hong_kong_fundamentals(parsed, ak)
    df = ak.stock_individual_info_em(symbol=parsed.akshare_code)
    return _format_frame(
        f"A-share company profile for {parsed.canonical}", "stock_individual_info_em", df
    )


def _get_financial_statement(
    ticker: str,
    freq: str,
    hk_statement: str,
    a_share_endpoint: str,
    title: str,
) -> str:
    parsed = _parse_symbol(ticker)
    ak = _akshare()
    if _is_hong_kong(parsed):
        indicator = (
            "\u5e74\u5ea6" if str(freq).lower() in {"annual", "yearly"} else "\u62a5\u544a\u671f"
        )
        source = "stock_financial_hk_report_em"
        df = getattr(ak, source)(
            stock=parsed.akshare_code,
            symbol=hk_statement,
            indicator=indicator,
        )
        if _empty(df):
            raise NoMarketDataError(ticker, parsed.canonical, f"no Hong Kong {title} rows")
        return _format_frame(f"Hong Kong {title} for {parsed.canonical}", source, df, limit=100)

    df = getattr(ak, a_share_endpoint)(symbol=parsed.prefixed_code)
    return _format_frame(f"A-share {title} for {parsed.canonical}", a_share_endpoint, df)


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    return _get_financial_statement(
        ticker,
        freq,
        "\u8d44\u4ea7\u8d1f\u503a\u8868",
        "stock_balance_sheet_by_report_em",
        "balance sheet",
    )


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    return _get_financial_statement(
        ticker,
        freq,
        "\u73b0\u91d1\u6d41\u91cf\u8868",
        "stock_cash_flow_sheet_by_report_em",
        "cash flow statement",
    )


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    return _get_financial_statement(
        ticker, freq, "\u5229\u6da6\u8868", "stock_profit_sheet_by_report_em", "income statement"
    )


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    parsed = _parse_symbol(ticker)
    ak = _akshare()
    df = ak.stock_news_em(symbol=parsed.akshare_code)
    if _empty(df):
        raise NoMarketDataError(ticker, parsed.canonical, "no AKShare stock news rows")

    date_col = next((col for col in ("发布时间", "时间", "日期") if col in df.columns), None)
    if date_col:
        df = _filter_by_date(df, date_col, start_date, end_date)
    return _format_frame(
        f"{_market_label(parsed)} news for {parsed.canonical}", "stock_news_em", df, limit=50
    )


def get_global_news(
    curr_date: str, look_back_days: int | None = None, limit: int | None = None
) -> str:
    ak = _akshare()
    rows = []
    for attr in ("stock_info_global_cls", "stock_info_global_futu"):
        fn = getattr(ak, attr, None)
        if fn is None:
            continue
        try:
            df = fn()
        except TypeError:
            df = fn(symbol="全部")
        if not _empty(df):
            rows.append((attr, df))
            break
    if not rows:
        return "DATA_UNAVAILABLE: AKShare global/mainland macro news endpoint returned no rows."

    source, df = rows[0]
    max_rows = limit or 10
    return _format_frame("China-relevant market and macro news", source, df, limit=max_rows)


def get_macro_data(indicator: str, curr_date: str, look_back_days: int | None = None) -> str:
    """Return China macro context from AKShare.

    The existing macro tool passes an indicator name. AKShare's macro endpoint
    names vary by release, so this first supports a small stable alias table and
    then falls back to market/macro headlines with an explicit source label.
    """
    ak = _akshare()
    aliases = {
        "cpi": "macro_china_cpi",
        "ppi": "macro_china_ppi",
        "pmi": "macro_china_pmi_yearly",
        "gdp": "macro_china_gdp",
        "lpr": "macro_china_lpr",
        "money_supply": "macro_china_money_supply",
        "social_financing": "macro_china_shrzgm",
    }
    key = str(indicator or "").strip().lower()
    endpoint = aliases.get(key)
    if endpoint and hasattr(ak, endpoint):
        df = getattr(ak, endpoint)()
        return _format_frame(f"China macro indicator: {indicator}", endpoint, df, limit=50)

    return get_global_news(curr_date, look_back_days=look_back_days, limit=10)


def get_insider_transactions(ticker: str) -> str:
    """Return A-share management/shareholder change context when available.

    This is the China-market counterpart to US insider transactions. Availability
    varies by AKShare release, so the function degrades to an explicit sentinel.
    """
    parsed = _parse_symbol(ticker)
    if _is_hong_kong(parsed):
        raise NoMarketDataError(
            ticker,
            parsed.canonical,
            "AKShare does not expose a stable Hong Kong insider-transactions endpoint",
        )
    ak = _akshare()
    for attr in ("stock_hold_management_detail_em", "stock_hold_control_cninfo"):
        fn = getattr(ak, attr, None)
        if fn is None:
            continue
        try:
            df = fn(symbol=parsed.akshare_code)
        except TypeError:
            df = fn()
        if not _empty(df):
            return _format_frame(
                f"A-share management/shareholder holding changes for {parsed.canonical}",
                attr,
                df,
                limit=50,
            )
    return (
        "DATA_UNAVAILABLE: AKShare did not expose a management/shareholder "
        f"holding-change endpoint for {parsed.canonical} in this environment."
    )

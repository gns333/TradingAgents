"""BaoStock fallback vendor for Mainland China market and financial data."""

from __future__ import annotations

import importlib
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator

import pandas as pd

from .china_symbol_utils import ChinaSymbol, parse_china_symbol
from .errors import NoMarketDataError, VendorError, VendorNotConfiguredError

logger = logging.getLogger(__name__)

_SESSION_LOCK = threading.Lock()
_HISTORY_FIELDS = "date,code,open,high,low,close,volume,amount"
_COLUMN_MAP = {
    "date": "Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
    "amount": "Amount",
}


class BaostockNotConfiguredError(VendorNotConfiguredError):
    """BaoStock was selected but its optional dependency is unavailable."""


class BaostockQueryError(VendorError):
    """BaoStock returned a non-zero login or query error code."""


def _baostock():
    try:
        return importlib.import_module("baostock")
    except ImportError as exc:
        raise BaostockNotConfiguredError(
            "BaoStock is required for Mainland China fallback data. Install it with "
            "`pip install 'tradingagents[china]'` or `pip install baostock`."
        ) from exc


def _parse_symbol(symbol: str) -> ChinaSymbol:
    parsed = parse_china_symbol(symbol)
    if parsed is None:
        raise NoMarketDataError(symbol, symbol, "not a Mainland China A-share symbol")
    return parsed


@contextmanager
def _session() -> Iterator[Any]:
    bs = _baostock()
    with _SESSION_LOCK:
        login = bs.login()
        if str(login.error_code) != "0":
            raise BaostockQueryError(f"BaoStock login failed: {login.error_msg}")
        try:
            yield bs
        finally:
            try:
                bs.logout()
            except Exception as exc:  # noqa: BLE001 - cleanup must preserve query outcome
                logger.warning("BaoStock logout failed: %s", exc)


def _frame_from_result(result: Any, title: str) -> pd.DataFrame:
    if str(result.error_code) != "0":
        raise BaostockQueryError(f"{title}: {result.error_msg}")
    rows = []
    while result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=list(result.fields))


def get_stock_frame(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Return normalized forward-adjusted A-share OHLCV rows."""
    parsed = _parse_symbol(symbol)
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    with _session() as bs:
        result = bs.query_history_k_data_plus(
            parsed.baostock_code,
            _HISTORY_FIELDS,
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2",
        )
        raw = _frame_from_result(result, f"BaoStock history query for {parsed.canonical}")
        if raw.empty:
            raise NoMarketDataError(
                symbol,
                parsed.canonical,
                f"BaoStock returned no rows between {start_date} and {end_date}",
            )

    data = raw.rename(columns=_COLUMN_MAP)
    wanted = ["Date", "Open", "High", "Low", "Close", "Volume", "Amount"]
    data = data[[column for column in wanted if column in data.columns]].copy()
    if "Date" in data.columns:
        data["Date"] = pd.to_datetime(data["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in [name for name in wanted if name != "Date" and name in data.columns]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if {"Date", "Close"} <= set(data.columns):
        data = data.dropna(subset=["Date", "Close"])
    if data.empty:
        raise NoMarketDataError(symbol, parsed.canonical, "BaoStock returned no usable OHLCV rows")
    data.attrs["baostock_source"] = "query_history_k_data_plus"
    return data


def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    """Return formatted A-share OHLCV history from BaoStock."""
    parsed = _parse_symbol(symbol)
    data = get_stock_frame(symbol, start_date, end_date)
    header = (
        f"# A-share stock data for {parsed.canonical} from {start_date} to {end_date}\n"
        "# Source: BaoStock query_history_k_data_plus\n"
        f"# Total records: {len(data)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + data.to_csv(index=False)


def get_indicator(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    """Compute supported technical indicators locally from BaoStock OHLCV."""
    parsed = _parse_symbol(symbol)
    key = indicator.lower()
    supported = {
        "close_10_ema",
        "close_50_sma",
        "close_200_sma",
        "rsi",
        "boll",
        "boll_ub",
        "boll_lb",
    }
    if key not in supported:
        raise ValueError(
            f"Indicator {indicator} is not supported by baostock. "
            f"Use one of: {', '.join(sorted(supported))}."
        )

    end = datetime.strptime(curr_date, "%Y-%m-%d")
    history_days = max(int(look_back_days), 450)
    start = (end - timedelta(days=history_days)).strftime("%Y-%m-%d")
    data = get_stock_frame(symbol, start, curr_date)
    close = data["Close"]

    if key.endswith("_sma"):
        window = int(key.split("_")[1])
        values = close.rolling(window).mean()
    elif key.endswith("_ema"):
        window = int(key.split("_")[1])
        values = close.ewm(span=window, adjust=False).mean()
    elif key == "rsi":
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        values = 100 - (100 / (1 + gain / loss))
    else:
        middle = close.rolling(20).mean()
        std = close.rolling(20).std()
        values = {
            "boll": middle,
            "boll_ub": middle + 2 * std,
            "boll_lb": middle - 2 * std,
        }[key]

    result = pd.DataFrame({"Date": data["Date"], indicator: values})
    result[indicator] = result[indicator].round(4)
    recent = result.tail(max(1, int(look_back_days)))
    return (
        f"## {indicator} values for {parsed.canonical} through {curr_date}\n"
        "# Source: BaoStock query_history_k_data_plus\n\n"
        + recent.to_csv(index=False)
    )


def _quarter_candidates(curr_date: str | None, count: int = 8) -> Iterator[tuple[int, int]]:
    reference = datetime.strptime(curr_date, "%Y-%m-%d") if curr_date else datetime.now()
    year = reference.year
    quarter = ((reference.month - 1) // 3) + 1
    for _ in range(count):
        yield year, quarter
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4


def _latest_financial_frame(
    bs: Any,
    query_name: str,
    symbol: ChinaSymbol,
    curr_date: str | None,
) -> pd.DataFrame:
    for year, quarter in _quarter_candidates(curr_date):
        result = getattr(bs, query_name)(
            code=symbol.baostock_code,
            year=year,
            quarter=quarter,
        )
        frame = _frame_from_result(result, f"BaoStock {query_name} for {symbol.canonical}")
        if not frame.empty:
            return frame
    return pd.DataFrame()


def _format_blocks(
    title: str,
    symbol: ChinaSymbol,
    blocks: list[tuple[str, pd.DataFrame]],
) -> str:
    usable = [(label, frame) for label, frame in blocks if frame is not None and not frame.empty]
    if not usable:
        raise NoMarketDataError(
            symbol.canonical,
            symbol.canonical,
            "BaoStock returned no usable financial rows",
        )
    parts = [f"# {title} for {symbol.canonical}", "# Source: BaoStock"]
    for label, frame in usable:
        parts.extend(["", f"## {label}", frame.to_csv(index=False)])
    return "\n".join(parts)


def get_fundamentals(ticker: str, curr_date: str | None = None) -> str:
    """Return all company and financial datasets BaoStock can provide."""
    parsed = _parse_symbol(ticker)
    with _session() as bs:
        basic = _frame_from_result(
            bs.query_stock_basic(code=parsed.baostock_code),
            f"BaoStock security metadata for {parsed.canonical}",
        )
        industry = _frame_from_result(
            bs.query_stock_industry(code=parsed.baostock_code),
            f"BaoStock industry for {parsed.canonical}",
        )
        blocks = [
            ("Security metadata", basic),
            ("Industry", industry),
            ("Profitability", _latest_financial_frame(bs, "query_profit_data", parsed, curr_date)),
            ("Growth", _latest_financial_frame(bs, "query_growth_data", parsed, curr_date)),
            ("Operation", _latest_financial_frame(bs, "query_operation_data", parsed, curr_date)),
            ("Solvency", _latest_financial_frame(bs, "query_balance_data", parsed, curr_date)),
            ("Cash flow", _latest_financial_frame(bs, "query_cash_flow_data", parsed, curr_date)),
            ("DuPont", _latest_financial_frame(bs, "query_dupont_data", parsed, curr_date)),
        ]
    return _format_blocks("BaoStock A-share fundamentals", parsed, blocks)


def _get_financial_report(
    ticker: str,
    curr_date: str | None,
    query_name: str,
    label: str,
) -> str:
    parsed = _parse_symbol(ticker)
    with _session() as bs:
        frame = _latest_financial_frame(bs, query_name, parsed, curr_date)
    return _format_blocks(label, parsed, [(label, frame)])


def get_balance_sheet(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Return BaoStock balance-sheet metrics for the latest available quarter."""
    _ = freq
    return _get_financial_report(
        ticker,
        curr_date,
        "query_balance_data",
        "BaoStock balance-sheet metrics",
    )


def get_cashflow(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Return BaoStock cash-flow metrics for the latest available quarter."""
    _ = freq
    return _get_financial_report(
        ticker,
        curr_date,
        "query_cash_flow_data",
        "BaoStock cash-flow metrics",
    )


def get_income_statement(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> str:
    """Return BaoStock profitability metrics, not a statutory income statement."""
    _ = freq
    return _get_financial_report(
        ticker,
        curr_date,
        "query_profit_data",
        "BaoStock profitability metrics (not a complete statutory income statement)",
    )

# BaoStock Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add BaoStock as an independent fallback vendor for all A-share market and financial capabilities it supports.

**Architecture:** A focused `baostock_stock.py` adapter owns lazy import, serialized login/query/logout sessions, symbol conversion, normalization, local indicators, and financial formatting. The existing vendor router remains responsible for fallback order, with the Mainland China profile selecting `akshare,baostock,yfinance` only for core, technical, and fundamental categories.

**Tech Stack:** Python 3.10+, pandas, BaoStock Python client, pytest/unittest mocks, existing TradingAgents vendor router.

## Global Constraints

- BaoStock must remain optional and be installed through `tradingagents[china]`.
- No account, token, environment variable, or administrator setting is required.
- BaoStock must not be registered for news, social sentiment, macro news, insider transactions, or prediction markets.
- Every successful result must identify `Source: BaoStock`.
- All tests must be hermetic and must not access BaoStock or other external services.
- Use targeted tests per task and one consolidated dataflow regression pass at the end.

## File Structure

- Create `tradingagents/dataflows/baostock_stock.py`: all BaoStock session, query, normalization, indicator, and financial adapter behavior.
- Create `tests/test_baostock_vendor.py`: hermetic adapter contract tests with a fake BaoStock module.
- Modify `tradingagents/dataflows/china_symbol_utils.py`: expose BaoStock code formatting on `ChinaSymbol`.
- Modify `tradingagents/dataflows/interface.py`: register BaoStock only for supported methods.
- Modify `tradingagents/default_config.py`: add BaoStock to Mainland China fallback chains.
- Modify `pyproject.toml`: add `baostock` to the `china` optional dependency.
- Modify `tests/test_china_symbol_utils.py`, `tests/test_vendor_routing.py`, and `tests/test_china_market_profile.py`: regression coverage for symbols, fallback order, and defaults.

---

### Task 1: BaoStock Session And Historical OHLCV

**Files:**
- Create: `tradingagents/dataflows/baostock_stock.py`
- Create: `tests/test_baostock_vendor.py`
- Modify: `tradingagents/dataflows/china_symbol_utils.py`
- Test: `tests/test_china_symbol_utils.py`
- Test: `tests/test_baostock_vendor.py`

**Interfaces:**
- Consumes: `parse_china_symbol(raw: str) -> ChinaSymbol | None`, `NoMarketDataError`, and `VendorNotConfiguredError`.
- Produces: `ChinaSymbol.baostock_code: str`, `get_stock_frame(symbol: str, start_date: str, end_date: str) -> pd.DataFrame`, and `get_stock(symbol: str, start_date: str, end_date: str) -> str`.

- [ ] **Step 1: Write failing symbol and adapter tests**

Add assertions for `ChinaSymbol.baostock_code`:

```python
self.assertEqual(parse_china_symbol("600519.SH").baostock_code, "sh.600519")
self.assertEqual(parse_china_symbol("000001.SZ").baostock_code, "sz.000001")
self.assertEqual(parse_china_symbol("430047.BJ").baostock_code, "bj.430047")
```

Create a reusable fake result and tests covering lazy-import failure, session cleanup, query arguments, normalized columns, and source metadata:

```python
class FakeResult:
    def __init__(self, fields, rows, error_code="0", error_msg=""):
        self.fields = fields
        self.rows = list(rows)
        self.error_code = error_code
        self.error_msg = error_msg
        self._index = -1

    def next(self):
        self._index += 1
        return self._index < len(self.rows)

    def get_row_data(self):
        return self.rows[self._index]


def test_stock_query_normalizes_rows_and_logs_out(monkeypatch):
    fake = mock.Mock()
    fake.login.return_value = mock.Mock(error_code="0", error_msg="")
    fake.query_history_k_data_plus.return_value = FakeResult(
        ["date", "code", "open", "high", "low", "close", "volume", "amount"],
        [["2026-01-05", "sh.600519", "100", "110", "99", "108", "12345", "23456"]],
    )
    monkeypatch.setattr(baostock_stock, "_baostock", lambda: fake)

    output = baostock_stock.get_stock("600519.SH", "2026-01-01", "2026-01-10")

    fake.query_history_k_data_plus.assert_called_once_with(
        "sh.600519",
        "date,code,open,high,low,close,volume,amount",
        start_date="2026-01-01",
        end_date="2026-01-10",
        frequency="d",
        adjustflag="2",
    )
    fake.logout.assert_called_once()
    assert "Source: BaoStock" in output
    assert "Date,Open,High,Low,Close,Volume,Amount" in output
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_china_symbol_utils.py tests/test_baostock_vendor.py -q
```

Expected: collection or assertion failure because `baostock_code` and `baostock_stock` do not exist.

- [ ] **Step 3: Implement symbol formatting, lazy import, session boundary, and OHLCV**

Add the property:

```python
@property
def baostock_code(self) -> str:
    return f"{self.exchange.lower()}.{self.code}"
```

Implement the adapter foundations:

```python
from contextlib import contextmanager
from datetime import datetime, timedelta
import importlib
import logging
import threading

import pandas as pd

from .china_symbol_utils import ChinaSymbol, parse_china_symbol
from .errors import NoMarketDataError, VendorNotConfiguredError

logger = logging.getLogger(__name__)
_SESSION_LOCK = threading.Lock()
_HISTORY_FIELDS = "date,code,open,high,low,close,volume,amount"


class BaostockNotConfiguredError(VendorNotConfiguredError):
    pass


class BaostockQueryError(RuntimeError):
    pass


def _baostock():
    try:
        return importlib.import_module("baostock")
    except ImportError as exc:
        raise BaostockNotConfiguredError(
            "BaoStock is required for Mainland China fallback data. Install it with "
            "`pip install 'tradingagents[china]'` or `pip install baostock`."
        ) from exc


@contextmanager
def _session():
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
            except Exception as exc:
                logger.warning("BaoStock logout failed: %s", exc)


def _frame_from_result(result, title: str) -> pd.DataFrame:
    if str(result.error_code) != "0":
        raise BaostockQueryError(f"{title}: {result.error_msg}")
    rows = []
    while result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=list(result.fields))
```

`get_stock_frame` queries with `adjustflag="2"`, renames fields to the canonical schema, converts dates/numbers, drops unusable rows, and raises `NoMarketDataError` when empty. `get_stock` adds canonical symbol, date range, `Source: BaoStock`, row count, retrieval time, and CSV output.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_china_symbol_utils.py tests/test_baostock_vendor.py -q
```

Expected: all Task 1 tests pass.

---

### Task 2: BaoStock Technical Indicators

**Files:**
- Modify: `tradingagents/dataflows/baostock_stock.py`
- Modify: `tests/test_baostock_vendor.py`

**Interfaces:**
- Consumes: `get_stock_frame(symbol, start_date, end_date) -> pd.DataFrame`.
- Produces: `get_indicator(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str`.

- [ ] **Step 1: Write failing indicator tests**

Use a deterministic 260-row frame and assert local output for EMA, SMA, RSI, and Bollinger variants, plus a clear failure for an unsupported name:

```python
@pytest.mark.parametrize(
    "indicator",
    ["close_10_ema", "close_50_sma", "close_200_sma", "rsi", "boll", "boll_ub", "boll_lb"],
)
def test_indicator_is_computed_from_baostock_frame(monkeypatch, indicator):
    frame = sample_ohlcv_frame(260)
    monkeypatch.setattr(baostock_stock, "get_stock_frame", lambda *args: frame)
    output = baostock_stock.get_indicator("600519.SH", indicator, "2026-07-10", 30)
    assert f"## {indicator} values for 600519.SH" in output
    assert "Source: BaoStock" in output


def test_indicator_rejects_unsupported_name():
    with pytest.raises(ValueError, match="not supported by baostock"):
        baostock_stock.get_indicator("600519.SH", "macd", "2026-07-10", 30)
```

- [ ] **Step 2: Run indicator tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_baostock_vendor.py -q
```

Expected: indicator tests fail because `get_indicator` is absent.

- [ ] **Step 3: Implement local indicator calculations**

Fetch at least 260 calendar days plus requested lookback, then compute the same formulas as the current China adapter:

```python
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
    values = {"boll": middle, "boll_ub": middle + 2 * std, "boll_lb": middle - 2 * std}[key]
```

Return the final `look_back_days` rows as CSV under a Markdown heading and `Source: BaoStock` line.

- [ ] **Step 4: Run indicator tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_baostock_vendor.py -q
```

Expected: all BaoStock adapter tests pass.

---

### Task 3: BaoStock Fundamental And Financial Data

**Files:**
- Modify: `tradingagents/dataflows/baostock_stock.py`
- Modify: `tests/test_baostock_vendor.py`

**Interfaces:**
- Consumes: `_session()` and `_frame_from_result(result, title)`.
- Produces: `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` with signatures matching `interface.py`.

- [ ] **Step 1: Write failing financial tests**

Create fake results for `query_stock_basic`, `query_stock_industry`, `query_profit_data`, `query_growth_data`, `query_operation_data`, `query_balance_data`, `query_cash_flow_data`, and `query_dupont_data`. Assert successful blocks are combined and labelled, empty optional blocks are omitted, and all-empty responses raise `NoMarketDataError`.

```python
def test_fundamentals_combines_available_baostock_datasets(monkeypatch):
    fake = fake_baostock_with_financial_results()
    monkeypatch.setattr(baostock_stock, "_baostock", lambda: fake)

    output = baostock_stock.get_fundamentals("600519.SH", "2026-07-10")

    assert "Source: BaoStock" in output
    assert "Security metadata" in output
    assert "Profitability" in output
    assert "Growth" in output
    fake.logout.assert_called_once()


def test_income_statement_is_labelled_as_profitability_metrics(monkeypatch):
    fake = fake_baostock_with_financial_results()
    monkeypatch.setattr(baostock_stock, "_baostock", lambda: fake)
    output = baostock_stock.get_income_statement("600519.SH", curr_date="2026-07-10")
    assert "BaoStock profitability metrics" in output
    assert "not a complete statutory income statement" in output
```

- [ ] **Step 2: Run financial tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_baostock_vendor.py -q
```

Expected: failures because financial adapter functions are absent.

- [ ] **Step 3: Implement report-period selection and financial functions**

Derive year and quarter from `curr_date`, trying the current quarter and earlier quarters until a non-empty result is found. Use the exact BaoStock signatures `query_*_data(code, year, quarter)` for financial metrics and `query_stock_basic(code=...)` / `query_stock_industry(code=...)` for metadata.

Use one shared formatter:

```python
def _format_blocks(title: str, canonical: str, blocks: list[tuple[str, pd.DataFrame]]) -> str:
    usable = [(label, frame) for label, frame in blocks if frame is not None and not frame.empty]
    if not usable:
        raise NoMarketDataError(canonical, canonical, "BaoStock returned no usable financial rows")
    parts = [f"# {title} for {canonical}", "# Source: BaoStock"]
    for label, frame in usable:
        parts.extend(["", f"## {label}", frame.to_csv(index=False)])
    return "\n".join(parts)
```

`get_fundamentals` combines all available blocks in one session. The three statement functions query and format only their mapped dataset. Empty datasets use `NoMarketDataError`; nonzero BaoStock error codes use `BaostockQueryError`.

- [ ] **Step 4: Run financial tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_baostock_vendor.py -q
```

Expected: all BaoStock tests pass.

---

### Task 4: Vendor Registry, China Profile, And Dependency

**Files:**
- Modify: `tradingagents/dataflows/interface.py`
- Modify: `tradingagents/default_config.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_vendor_routing.py`
- Modify: `tests/test_china_market_profile.py`
- Modify: `tests/test_web_runner.py`

**Interfaces:**
- Consumes: all six public functions from `baostock_stock.py`.
- Produces: explicit `akshare,baostock,yfinance` chains for core, technical, and fundamental methods.

- [ ] **Step 1: Write failing routing and configuration tests**

Update the China-profile assertions:

```python
self.assertEqual(
    dc.DEFAULT_CONFIG["data_vendors"]["core_stock_apis"],
    "akshare,baostock,yfinance",
)
self.assertEqual(
    dc.DEFAULT_CONFIG["data_vendors"]["technical_indicators"],
    "akshare,baostock,yfinance",
)
self.assertEqual(
    dc.DEFAULT_CONFIG["data_vendors"]["fundamental_data"],
    "akshare,baostock,yfinance",
)
```

Add a routing-order test:

```python
def test_china_chain_uses_baostock_after_akshare_failure(self):
    set_config({"data_vendors": {"core_stock_apis": "akshare,baostock,yfinance"}})
    yahoo = mock.Mock(return_value="YAHOO")
    with self._route({
        "akshare": _raises(ConnectionError("eastmoney down")),
        "baostock": _returns("BAOSTOCK"),
        "yfinance": yahoo,
    }):
        result = interface.route_to_vendor(
            "get_stock_data", "600519.SH", "2026-01-01", "2026-01-10"
        )
    self.assertEqual(result, "BAOSTOCK")
    yahoo.assert_not_called()
```

Assert `baostock` appears in the `china` dependency list and that Web A-share request config resolves to the new chains.

- [ ] **Step 2: Run config/routing tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_vendor_routing.py tests/test_china_market_profile.py tests/test_web_runner.py -q
```

Expected: failures because BaoStock is not registered or configured.

- [ ] **Step 3: Register BaoStock and update configuration**

Import the six BaoStock functions in `interface.py`, add `baostock` to `VENDOR_LIST`, and register it only under:

```python
"get_stock_data"
"get_indicators"
"get_fundamentals"
"get_balance_sheet"
"get_cashflow"
"get_income_statement"
```

Update `MARKET_PROFILES["china_mainland"]["data_vendors"]` so core, technical, and fundamental values are `akshare,baostock,yfinance`; leave news, macro, and prediction-market values unchanged. Add `"baostock"` beside `"akshare"` in `project.optional-dependencies.china`.

- [ ] **Step 4: Run config/routing tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_vendor_routing.py tests/test_china_market_profile.py tests/test_web_runner.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Run consolidated touched-surface verification**

Run:

```bash
python3 -m pytest \
  tests/test_baostock_vendor.py \
  tests/test_akshare_vendor.py \
  tests/test_china_symbol_utils.py \
  tests/test_market_data_validator_china.py \
  tests/test_vendor_routing.py \
  tests/test_china_market_profile.py \
  tests/test_web_runner.py -q
python3 -m ruff check \
  tradingagents/dataflows/baostock_stock.py \
  tradingagents/dataflows/china_symbol_utils.py \
  tradingagents/dataflows/interface.py \
  tradingagents/default_config.py \
  tests/test_baostock_vendor.py
```

Expected: all tests pass and Ruff reports no errors.

- [ ] **Step 6: Restart and health-check the local Web server**

Restart `tradingagents-web --host 127.0.0.1 --port 8000`, then request `/healthz`. Expected: HTTP 200. Do not perform an external BaoStock integration query as part of automated verification; it would make the test outcome network-dependent.

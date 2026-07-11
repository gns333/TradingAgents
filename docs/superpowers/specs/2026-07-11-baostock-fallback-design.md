# BaoStock Fallback Design

## Goal

Add BaoStock as an independent Mainland China data vendor so an A-share analysis can continue when AKShare's upstream endpoints are unavailable. BaoStock backs up every capability it actually provides: historical OHLCV, locally computed technical indicators, company metadata, and available financial metrics. It does not claim coverage for news, social sentiment, macro news, insider transactions, or prediction markets.

## Chosen Approach

Implement BaoStock as a peer vendor in the existing `route_to_vendor` registry. This preserves the current explicit ordered-chain behavior and keeps provider failures, source labels, and tests isolated. Do not hide BaoStock inside the AKShare adapter and do not introduce a new background cache service in this change.

The Mainland China profile will use these chains:

- Core stock data: `akshare,baostock,yfinance`
- Technical indicators: `akshare,baostock,yfinance`
- Fundamental data: `akshare,baostock,yfinance`
- News and unsupported categories: unchanged

## Components

### BaoStock Adapter

Create `tradingagents/dataflows/baostock_stock.py` with lazy dependency loading and the same public function signatures used by the vendor registry:

- `get_stock`
- `get_indicator`
- `get_fundamentals`
- `get_balance_sheet`
- `get_cashflow`
- `get_income_statement`

The adapter converts canonical A-share symbols such as `600519.SH` to BaoStock codes such as `sh.600519`. It rejects non-A-share symbols with `NoMarketDataError`.

### Session Boundary

BaoStock uses module-level login state. Each adapter operation acquires a process lock, calls `login()`, performs its query or query group, and calls `logout()` in `finally`. This prevents concurrent Web analyses from corrupting a shared session and guarantees cleanup after query failures.

### Result Normalization

Historical rows are normalized to `Date`, `Open`, `High`, `Low`, `Close`, `Volume`, and `Amount`. Date filtering remains inclusive, and the adapter requests forward-adjusted data to match the existing AKShare path. Empty or unusable results raise `NoMarketDataError`.

Every successful formatted result includes `Source: BaoStock`, the canonical symbol, requested date range where applicable, and row count. BaoStock output never masquerades as AKShare or Yahoo data.

### Technical Indicators

BaoStock supplies OHLCV only; indicators are computed locally from that frame. Support matches the current China path: `close_10_ema`, `close_50_sma`, `close_200_sma`, `rsi`, `boll`, `boll_ub`, and `boll_lb`. Unsupported indicator names raise a clear `ValueError` so the existing tool can report them explicitly.

### Fundamentals

BaoStock's available financial APIs are mapped honestly:

- `get_fundamentals`: security metadata plus profitability, growth, operation, solvency, and DuPont metrics when available
- `get_balance_sheet`: balance-sheet metrics
- `get_cashflow`: cash-flow metrics
- `get_income_statement`: profitability metrics, explicitly labelled as BaoStock profitability data rather than a complete statutory income statement

Financial queries use the latest report period on or before `curr_date` when a date is supplied. Missing individual datasets do not erase successful datasets in the same fundamentals response; if every requested dataset is empty, the adapter raises `NoMarketDataError`.

## Dependency And Configuration

Add `baostock` to the existing `china` optional dependency group. The core package remains free of China-specific dependencies. If BaoStock is selected but not installed, the adapter raises `VendorNotConfiguredError` with the existing `tradingagents[china]` installation command.

Register `baostock` only for methods it implements. Add it to `VENDOR_LIST` and update the `china_mainland` profile chains for core, indicators, and fundamentals. No environment variable, account, API key, or administrator setting is required.

## Error Handling

- Import failure: `VendorNotConfiguredError`; router continues to the next configured vendor.
- Login or query error code: a BaoStock-specific vendor error containing the API message; router logs it and continues.
- Empty or unusable rows: `NoMarketDataError`; router may try the next configured vendor.
- Unexpected exception: propagated to the router after session cleanup.
- Logout failure: logged without replacing the original query outcome.

The adapter must not invoke AKShare, Sina, or MiniRacer internally. Fallback remains the responsibility of the existing vendor router.

## Testing

All BaoStock tests are hermetic and use a fake module; they do not access external services.

- Symbol conversion for Shanghai, Shenzhen, and Beijing stocks
- Missing dependency error
- Login/query/logout lifecycle, including cleanup after failure
- Query error-code handling
- OHLCV normalization, source metadata, date range, and adjustment mode
- Empty data handling
- Local indicator calculation and unsupported indicators
- Fundamentals, balance, cash flow, and profitability formatting
- Explicit `AKShare -> BaoStock -> Yahoo` routing order
- Mainland China profile defaults and optional dependency declaration

Run focused BaoStock, vendor-routing, China-profile, and Web-runner tests during development, followed by one consolidated regression pass for the touched dataflow surface.

## Out Of Scope

- BaoStock-backed news, social sentiment, macro news, insider transactions, or prediction markets
- Tushare integration
- Background synchronization or persistent market-data cache
- UI controls for selecting vendors
- Changes to scoring, agent prompts, or report presentation

## Success Criteria

When an A-share AKShare call fails, the router calls BaoStock next and returns clearly labelled BaoStock data when available. The Web service remains alive, unsupported categories keep their existing behavior, and non-A-share analysis remains unchanged.

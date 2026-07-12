"""BaoStock vendor integration."""

from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows import baostock_stock
from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError


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


def _fake_baostock(result):
    fake = mock.Mock()
    fake.login.return_value = mock.Mock(error_code="0", error_msg="")
    fake.query_history_k_data_plus.return_value = result
    return fake


def _sample_ohlcv_frame(rows=260):
    dates = pd.bdate_range(end="2026-07-10", periods=rows)
    return pd.DataFrame(
        {
            "Date": dates.strftime("%Y-%m-%d"),
            "Open": [100 + index for index in range(rows)],
            "High": [101 + index for index in range(rows)],
            "Low": [99 + index for index in range(rows)],
            "Close": [100.5 + index for index in range(rows)],
            "Volume": [1_000_000 + index for index in range(rows)],
            "Amount": [10_000_000 + index for index in range(rows)],
        }
    )


def _rows_result(label, value="1.23"):
    return FakeResult(["code", "pubDate", "statDate", label], [["sh.600519", "2026-06-30", "2026-06-30", value]])


def _fake_baostock_with_financial_results():
    fake = mock.Mock()
    fake.login.return_value = mock.Mock(error_code="0", error_msg="")
    fake.query_stock_basic.return_value = FakeResult(
        ["code", "code_name", "ipoDate", "type", "status"],
        [["sh.600519", "贵州茅台", "2001-08-27", "1", "1"]],
    )
    fake.query_stock_industry.return_value = FakeResult(
        ["code", "code_name", "industry", "industryClassification"],
        [["sh.600519", "贵州茅台", "白酒", "申万一级行业"]],
    )
    fake.query_profit_data.return_value = _rows_result("roeAvg")
    fake.query_growth_data.return_value = _rows_result("YOYNI")
    fake.query_operation_data.return_value = _rows_result("NRTurnRatio")
    fake.query_balance_data.return_value = _rows_result("currentRatio")
    fake.query_cash_flow_data.return_value = _rows_result("CFOToOR")
    fake.query_dupont_data.return_value = _rows_result("dupontROE")
    return fake


def test_missing_baostock_dependency_is_actionable(monkeypatch):
    monkeypatch.setattr(baostock_stock.importlib, "import_module", mock.Mock(side_effect=ImportError))

    with pytest.raises(VendorNotConfiguredError, match=r"tradingagents\[china\]"):
        baostock_stock.get_stock("600519.SH", "2026-01-01", "2026-01-10")


def test_stock_query_normalizes_rows_and_logs_out(monkeypatch):
    result = FakeResult(
        ["date", "code", "open", "high", "low", "close", "volume", "amount"],
        [["2026-01-05", "sh.600519", "100", "110", "99", "108", "12345", "23456"]],
    )
    fake = _fake_baostock(result)
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
    assert "A-share stock data for 600519.SH" in output
    assert "Source: BaoStock" in output
    assert "Date,Open,High,Low,Close,Volume,Amount" in output
    assert "2026-01-05,100,110,99,108,12345,23456" in output


def test_stock_query_error_logs_out(monkeypatch):
    fake = _fake_baostock(FakeResult([], [], error_code="1001", error_msg="query failed"))
    monkeypatch.setattr(baostock_stock, "_baostock", lambda: fake)

    with pytest.raises(baostock_stock.BaostockQueryError, match="query failed"):
        baostock_stock.get_stock("600519.SH", "2026-01-01", "2026-01-10")

    fake.logout.assert_called_once()


def test_stock_query_rejects_empty_rows(monkeypatch):
    fields = ["date", "code", "open", "high", "low", "close", "volume", "amount"]
    fake = _fake_baostock(FakeResult(fields, []))
    monkeypatch.setattr(baostock_stock, "_baostock", lambda: fake)

    with pytest.raises(NoMarketDataError, match="no rows"):
        baostock_stock.get_stock("600519.SH", "2026-01-01", "2026-01-10")

    fake.logout.assert_called_once()


@pytest.mark.parametrize(
    "indicator",
    [
        "close_10_ema",
        "close_50_sma",
        "close_200_sma",
        "macd",
        "macds",
        "macdh",
        "rsi",
        "boll",
        "boll_ub",
        "boll_lb",
        "atr",
        "vwma",
    ],
)
def test_indicator_is_computed_from_baostock_frame(monkeypatch, indicator):
    monkeypatch.setattr(baostock_stock, "get_stock_frame", lambda *args: _sample_ohlcv_frame())

    output = baostock_stock.get_indicator("600519.SH", indicator, "2026-07-10", 30)

    assert f"## {indicator} values for 600519.SH" in output
    assert "Source: BaoStock" in output
    assert "2026-07-10" in output


def test_baostock_indicator_formulas_match_standard_definitions(monkeypatch):
    frame = _sample_ohlcv_frame()
    monkeypatch.setattr(baostock_stock, "get_stock_frame", lambda *args: frame)

    close = frame["Close"]
    expected_macd = close.ewm(span=12, adjust=False).mean() - close.ewm(
        span=26, adjust=False
    ).mean()
    expected_macds = expected_macd.ewm(span=9, adjust=False).mean()
    expected_macdh = expected_macd - expected_macds
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            frame["High"] - frame["Low"],
            (frame["High"] - previous_close).abs(),
            (frame["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    expected_atr = true_range.rolling(14).mean()
    expected_vwma = (close * frame["Volume"]).rolling(20).sum() / frame[
        "Volume"
    ].rolling(20).sum()

    expected = {
        "macd": expected_macd.iloc[-1],
        "macds": expected_macds.iloc[-1],
        "macdh": expected_macdh.iloc[-1],
        "atr": expected_atr.iloc[-1],
        "vwma": expected_vwma.iloc[-1],
    }
    for indicator, value in expected.items():
        output = baostock_stock.get_indicator(
            "600519.SH", indicator, "2026-07-10", 1
        )
        last_value = float(output.strip().splitlines()[-1].split(",")[-1])
        assert last_value == pytest.approx(round(value, 4))


def test_indicator_rejects_unsupported_name():
    with pytest.raises(ValueError, match="not supported by baostock"):
        baostock_stock.get_indicator("600519.SH", "stochrsi", "2026-07-10", 30)


def test_fundamentals_combines_available_baostock_datasets(monkeypatch):
    fake = _fake_baostock_with_financial_results()
    monkeypatch.setattr(baostock_stock, "_baostock", lambda: fake)

    output = baostock_stock.get_fundamentals("600519.SH", "2026-07-10")

    assert "Source: BaoStock" in output
    assert "Security metadata" in output
    assert "Industry" in output
    assert "Profitability" in output
    assert "Growth" in output
    assert "Operation" in output
    assert "Solvency" in output
    assert "Cash flow" in output
    assert "DuPont" in output
    fake.logout.assert_called_once()


@pytest.mark.parametrize(
    ("function_name", "query_name", "expected_label"),
    [
        ("get_balance_sheet", "query_balance_data", "balance-sheet metrics"),
        ("get_cashflow", "query_cash_flow_data", "cash-flow metrics"),
        ("get_income_statement", "query_profit_data", "profitability metrics"),
    ],
)
def test_financial_functions_map_to_supported_baostock_queries(
    monkeypatch, function_name, query_name, expected_label
):
    fake = _fake_baostock_with_financial_results()
    monkeypatch.setattr(baostock_stock, "_baostock", lambda: fake)

    output = getattr(baostock_stock, function_name)(
        "600519.SH", freq="quarterly", curr_date="2026-07-10"
    )

    assert "Source: BaoStock" in output
    assert expected_label in output
    getattr(fake, query_name).assert_called()
    fake.logout.assert_called_once()
    if function_name == "get_income_statement":
        assert "not a complete statutory income statement" in output


def test_fundamentals_rejects_all_empty_datasets(monkeypatch):
    fake = _fake_baostock_with_financial_results()
    empty = FakeResult(["code"], [])
    for query_name in (
        "query_stock_basic",
        "query_stock_industry",
        "query_profit_data",
        "query_growth_data",
        "query_operation_data",
        "query_balance_data",
        "query_cash_flow_data",
        "query_dupont_data",
    ):
        getattr(fake, query_name).return_value = empty
    monkeypatch.setattr(baostock_stock, "_baostock", lambda: fake)

    with pytest.raises(NoMarketDataError, match="financial rows"):
        baostock_stock.get_fundamentals("600519.SH", "2026-07-10")

    fake.logout.assert_called_once()

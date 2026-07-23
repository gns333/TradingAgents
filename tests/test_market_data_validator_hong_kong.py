"""Hong Kong market-data snapshot vendor fallback."""

import pandas as pd
import pytest

import tradingagents.dataflows.market_data_validator as validator


def test_hong_kong_uses_yfinance_after_akshare_disconnect(monkeypatch):
    expected = pd.DataFrame(
        {
            "Date": ["2026-05-20"],
            "Open": [400.0],
            "High": [410.0],
            "Low": [398.0],
            "Close": [408.0],
            "Volume": [12345],
        }
    )
    monkeypatch.setattr(
        validator,
        "get_config",
        lambda: {"data_vendors": {"core_stock_apis": "akshare,yfinance"}},
    )
    monkeypatch.setattr(
        validator,
        "get_akshare_stock_frame",
        lambda *args: (_ for _ in ()).throw(ConnectionError("eastmoney down")),
    )
    monkeypatch.setattr(validator, "get_baostock_stock_frame", pytest.fail)
    monkeypatch.setattr(validator, "load_ohlcv", lambda *args: expected)

    result = validator._load_ohlcv("0700.HK", "2026-05-20")

    pd.testing.assert_frame_equal(result, expected)

"""China-market coverage for deterministic market-data snapshots."""

import unittest
from unittest import mock

import pandas as pd

import tradingagents.dataflows.akshare_stock as akshare_stock
import tradingagents.dataflows.market_data_validator as validator


class ChinaMarketDataValidatorTests(unittest.TestCase):
    def test_a_share_snapshot_uses_akshare_not_yahoo(self):
        chinese_rows = pd.DataFrame({
            "日期": pd.bdate_range("2026-04-01", "2026-05-20").strftime("%Y-%m-%d"),
            "开盘": [100 + i for i in range(36)],
            "最高": [101 + i for i in range(36)],
            "最低": [99 + i for i in range(36)],
            "收盘": [100.5 + i for i in range(36)],
            "成交量": [1_000_000 + i for i in range(36)],
        })
        fake_ak = mock.Mock()
        fake_ak.stock_zh_a_hist.return_value = chinese_rows

        def fail_if_yahoo_is_used(symbol, curr_date):
            raise AssertionError("A-share verification snapshot should not use Yahoo load_ohlcv")

        with mock.patch.object(validator, "load_ohlcv", side_effect=fail_if_yahoo_is_used), \
                mock.patch.object(akshare_stock, "_akshare", return_value=fake_ak):
            snap = validator.build_verified_market_snapshot("600519.SH", "2026-05-16")

        fake_ak.stock_zh_a_hist.assert_called_once()
        self.assertIn("Verified market data snapshot for 600519.SH", snap)
        self.assertIn("Latest trading row used: 2026-05-15", snap)
        self.assertIn("| Close | 132.50 |", snap)


if __name__ == "__main__":
    unittest.main()

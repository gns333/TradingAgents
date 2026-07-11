"""AKShare vendor integration."""

import unittest
from unittest import mock

from tradingagents.dataflows.errors import VendorNotConfiguredError


class AkshareVendorTests(unittest.TestCase):
    def test_missing_akshare_dependency_is_actionable(self):
        from tradingagents.dataflows import akshare_stock

        with mock.patch.object(akshare_stock.importlib, "import_module", side_effect=ImportError):
            with self.assertRaises(VendorNotConfiguredError) as ctx:
                akshare_stock.get_stock("600519.SH", "2026-01-01", "2026-01-10")

        self.assertIn("pip install", str(ctx.exception))
        self.assertIn("akshare", str(ctx.exception).lower())

    def test_stock_history_formats_akshare_dataframe(self):
        from tradingagents.dataflows import akshare_stock

        fake_df = akshare_stock.pd.DataFrame(
            [
                {
                    "日期": "2026-01-05",
                    "开盘": 100.0,
                    "最高": 110.0,
                    "最低": 99.0,
                    "收盘": 108.0,
                    "成交量": 12345,
                    "成交额": 23456.0,
                    "换手率": 1.23,
                }
            ]
        )
        fake_ak = mock.Mock()
        fake_ak.stock_zh_a_hist.return_value = fake_df

        with mock.patch.object(akshare_stock, "_akshare", return_value=fake_ak):
            out = akshare_stock.get_stock("600519.SH", "2026-01-01", "2026-01-10")

        fake_ak.stock_zh_a_hist.assert_called_once_with(
            symbol="600519",
            period="daily",
            start_date="20260101",
            end_date="20260110",
            adjust="qfq",
        )
        self.assertIn("# A-share stock data for 600519.SH", out)
        self.assertIn("Date,Open,High,Low,Close,Volume,Amount,Turnover", out)
        self.assertIn("2026-01-05,100.0,110.0,99.0,108.0,12345,23456.0,1.23", out)

    def test_stock_history_does_not_call_sina_fallback_when_eastmoney_disconnects(self):
        from tradingagents.dataflows import akshare_stock

        fake_ak = mock.Mock()
        error = ConnectionError("Remote end closed connection without response")
        fake_ak.stock_zh_a_hist.side_effect = error

        with mock.patch.object(akshare_stock, "_akshare", return_value=fake_ak):
            with self.assertRaisesRegex(ConnectionError, "Remote end closed"):
                akshare_stock.get_stock("600519.SH", "2026-01-01", "2026-01-10")

        fake_ak.stock_zh_a_daily.assert_not_called()


if __name__ == "__main__":
    unittest.main()

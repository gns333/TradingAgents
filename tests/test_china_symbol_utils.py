"""China A-share symbol normalization helpers."""

import unittest

from tradingagents.dataflows.china_symbol_utils import (
    ChinaSymbol,
    parse_china_symbol,
)


class ChinaSymbolUtilsTests(unittest.TestCase):
    def test_parse_exchange_suffixed_a_share_symbols(self):
        self.assertEqual(
            parse_china_symbol("600519.SH"),
            ChinaSymbol(code="600519", exchange="SH"),
        )
        self.assertEqual(
            parse_china_symbol("600519.SS"),
            ChinaSymbol(code="600519", exchange="SH"),
        )
        self.assertEqual(
            parse_china_symbol("000001.SZ"),
            ChinaSymbol(code="000001", exchange="SZ"),
        )
        self.assertEqual(
            parse_china_symbol("430047.BJ"),
            ChinaSymbol(code="430047", exchange="BJ"),
        )

    def test_parse_prefixed_and_bare_a_share_symbols(self):
        self.assertEqual(
            parse_china_symbol("sh600519"),
            ChinaSymbol(code="600519", exchange="SH"),
        )
        self.assertEqual(
            parse_china_symbol("SZ000001"),
            ChinaSymbol(code="000001", exchange="SZ"),
        )
        self.assertEqual(
            parse_china_symbol("600519"),
            ChinaSymbol(code="600519", exchange="SH"),
        )
        self.assertEqual(
            parse_china_symbol("300750"),
            ChinaSymbol(code="300750", exchange="SZ"),
        )
        self.assertEqual(
            parse_china_symbol("835185"),
            ChinaSymbol(code="835185", exchange="BJ"),
        )

    def test_parse_corrects_mismatched_exchange_suffix_from_code_prefix(self):
        self.assertEqual(
            parse_china_symbol("002624.SH"),
            ChinaSymbol(code="002624", exchange="SZ"),
        )
        self.assertEqual(
            parse_china_symbol("600519.SZ"),
            ChinaSymbol(code="600519", exchange="SH"),
        )

    def test_symbol_formats_for_common_vendors(self):
        symbol = parse_china_symbol("600519.SH")

        self.assertEqual(symbol.akshare_code, "600519")
        self.assertEqual(symbol.baostock_code, "sh.600519")
        self.assertEqual(symbol.tushare_code, "600519.SH")
        self.assertEqual(symbol.yahoo_code, "600519.SS")
        self.assertEqual(symbol.prefixed_code, "SH600519")

        self.assertEqual(parse_china_symbol("000001.SZ").baostock_code, "sz.000001")
        self.assertEqual(parse_china_symbol("430047.BJ").baostock_code, "bj.430047")

    def test_non_a_share_symbols_return_none(self):
        self.assertIsNone(parse_china_symbol("AAPL"))
        self.assertIsNone(parse_china_symbol("0700.HK"))
        self.assertIsNone(parse_china_symbol("BTC-USD"))


if __name__ == "__main__":
    unittest.main()

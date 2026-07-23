"""Hong Kong stock symbol normalization."""

from tradingagents.dataflows.hong_kong_symbol_utils import parse_hong_kong_symbol


def test_hong_kong_symbol_exposes_vendor_specific_codes():
    symbol = parse_hong_kong_symbol("00700.HK")

    assert symbol is not None
    assert symbol.canonical == "0700.HK"
    assert symbol.akshare_code == "00700"
    assert symbol.yahoo_code == "0700.HK"


def test_hong_kong_symbol_accepts_four_and_five_digit_codes():
    assert parse_hong_kong_symbol("0700.hk").akshare_code == "00700"
    assert parse_hong_kong_symbol("09988.HK").canonical == "9988.HK"
    assert parse_hong_kong_symbol("80020.HK").canonical == "80020.HK"


def test_hong_kong_symbol_requires_hk_suffix():
    assert parse_hong_kong_symbol("0700") is None
    assert parse_hong_kong_symbol("600519.SH") is None

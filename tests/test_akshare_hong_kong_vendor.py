"""AKShare Hong Kong vendor integration."""

from unittest import mock

from tradingagents.dataflows import akshare_stock


def test_hong_kong_stock_history_uses_five_digit_akshare_code():
    fake_df = akshare_stock.pd.DataFrame(
        [
            {
                "\u65e5\u671f": "2026-01-05",
                "\u5f00\u76d8": 400.0,
                "\u6700\u9ad8": 410.0,
                "\u6700\u4f4e": 398.0,
                "\u6536\u76d8": 408.0,
                "\u6210\u4ea4\u91cf": 12345,
                "\u6210\u4ea4\u989d": 23456.0,
                "\u6362\u624b\u7387": 1.23,
            }
        ]
    )
    fake_ak = mock.Mock()
    fake_ak.stock_hk_hist.return_value = fake_df

    with mock.patch.object(akshare_stock, "_akshare", return_value=fake_ak):
        out = akshare_stock.get_stock("0700.HK", "2026-01-01", "2026-01-10")

    fake_ak.stock_hk_hist.assert_called_once_with(
        symbol="00700",
        period="daily",
        start_date="20260101",
        end_date="20260110",
        adjust="qfq",
    )
    fake_ak.stock_zh_a_hist.assert_not_called()
    assert "# Hong Kong stock data for 0700.HK" in out
    assert "2026-01-05,400.0,410.0,398.0,408.0,12345,23456.0,1.23" in out


def test_hong_kong_financial_statements_use_hk_report_endpoint():
    fake_ak = mock.Mock()
    fake_ak.stock_financial_hk_report_em.return_value = akshare_stock.pd.DataFrame(
        [{"REPORT_DATE": "2025-12-31", "STD_ITEM_NAME": "\u603b\u8d44\u4ea7", "AMOUNT": 100}]
    )

    with mock.patch.object(akshare_stock, "_akshare", return_value=fake_ak):
        out = akshare_stock.get_balance_sheet("00700.HK")

    fake_ak.stock_financial_hk_report_em.assert_called_once_with(
        stock="00700",
        symbol="\u8d44\u4ea7\u8d1f\u503a\u8868",
        indicator="\u62a5\u544a\u671f",
    )
    assert "# Hong Kong balance sheet for 0700.HK" in out


def test_hong_kong_fundamentals_combine_profiles_and_indicators():
    fake_ak = mock.Mock()
    fake_ak.stock_hk_security_profile_em.return_value = akshare_stock.pd.DataFrame(
        [{"\u8bc1\u5238\u7b80\u79f0": "\u817e\u8baf\u63a7\u80a1"}]
    )
    fake_ak.stock_hk_company_profile_em.return_value = akshare_stock.pd.DataFrame(
        [{"\u6240\u5c5e\u884c\u4e1a": "\u4e92\u8054\u7f51"}]
    )
    fake_ak.stock_hk_financial_indicator_em.return_value = akshare_stock.pd.DataFrame(
        [{"\u5e02\u76c8\u7387": 20.0}]
    )

    with mock.patch.object(akshare_stock, "_akshare", return_value=fake_ak):
        out = akshare_stock.get_fundamentals("0700.HK")

    for endpoint in (
        fake_ak.stock_hk_security_profile_em,
        fake_ak.stock_hk_company_profile_em,
        fake_ak.stock_hk_financial_indicator_em,
    ):
        endpoint.assert_called_once_with(symbol="00700")
    assert "\u817e\u8baf\u63a7\u80a1" in out
    assert "\u4e92\u8054\u7f51" in out
    assert "20.0" in out

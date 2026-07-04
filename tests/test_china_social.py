from unittest import mock

import pandas as pd

from tradingagents.dataflows import china_social


def _df(source: str, code: str = "SH600519") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"代码": code, "名称": "贵州茅台", "来源": source, "热度": 98},
            {"代码": "SZ000001", "名称": "平安银行", "来源": source, "热度": 20},
        ]
    )


class _FakeAkshare:
    def __getattr__(self, name):
        def endpoint(**kwargs):
            return _df(name)

        return endpoint


def test_fetch_china_social_sentiment_includes_requested_mainland_sources():
    fake_ak = _FakeAkshare()

    with mock.patch.object(china_social, "_akshare", return_value=fake_ak):
        out = china_social.fetch_china_social_sentiment(
            "600519.SH",
            "2026-06-28",
            "2026-07-05",
            limit=5,
        )

    assert "Sources limited to: 东方财富、雪球、同花顺." in out
    assert "东方财富股吧人气榜-最新排名" in out
    assert "雪球讨论热度榜命中" in out
    assert "同花顺量价齐升榜命中" in out
    assert "stock_hot_rank_latest_em" in out
    assert "stock_hot_tweet_xq" in out
    assert "stock_rank_ljqs_ths" in out
    assert "SH600519" in out


def test_fetch_china_social_sentiment_degrades_per_endpoint():
    fake_ak = _FakeAkshare()

    def fail_once(**kwargs):
        raise ConnectionError("upstream closed")

    fake_ak.stock_hot_rank_latest_em = fail_once

    with mock.patch.object(china_social, "_akshare", return_value=fake_ak):
        out = china_social.fetch_china_social_sentiment(
            "600519.SH",
            "2026-06-28",
            "2026-07-05",
            limit=5,
        )

    assert "DATA_UNAVAILABLE: ConnectionError: upstream closed" in out
    assert "雪球讨论热度榜命中" in out


def test_fetch_china_social_sentiment_rejects_non_a_share_symbol():
    out = china_social.fetch_china_social_sentiment("NVDA", "2026-06-28", "2026-07-05")

    assert "not a Mainland China A-share symbol" in out

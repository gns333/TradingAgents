"""Mainland China social and heat signals for A-share sentiment analysis.

The integration intentionally stays on AKShare-backed public endpoints. Some
providers expose heat rankings rather than full community post streams; the
rendered blocks call that out explicitly so the LLM does not treat rankings as
verbatim social posts.
"""

from __future__ import annotations

import re
from typing import Callable

import pandas as pd

from .akshare_stock import _akshare, _empty, _format_frame
from .china_symbol_utils import ChinaSymbol, parse_china_symbol


_DEFAULT_LIMIT = 20


def _symbol_terms(symbol: ChinaSymbol) -> tuple[str, ...]:
    return (
        symbol.code,
        symbol.canonical,
        symbol.prefixed_code,
        symbol.prefixed_code.lower(),
        symbol.yahoo_code,
    )


def _filter_symbol_rows(df: pd.DataFrame, symbol: ChinaSymbol) -> pd.DataFrame:
    """Keep ranking rows that mention the target symbol in any column."""
    if _empty(df):
        return pd.DataFrame()

    pattern = "|".join(re.escape(term) for term in _symbol_terms(symbol))
    text = df.astype(str)
    mask = text.apply(
        lambda col: col.str.contains(pattern, case=False, na=False, regex=True),
        axis=0,
    ).any(axis=1)
    return df.loc[mask].copy()


def _render_unavailable(title: str, source: str, reason: str) -> str:
    return f"# {title}\n# Source: AKShare {source}\n\nDATA_UNAVAILABLE: {reason}"


def _call_ak(ak, source: str, **kwargs) -> pd.DataFrame:
    fn = getattr(ak, source, None)
    if fn is None:
        raise AttributeError(f"AKShare endpoint {source} is not available")
    return fn(**kwargs)


def _render_endpoint(
    *,
    title: str,
    source: str,
    fetch: Callable[[], pd.DataFrame],
    limit: int,
    symbol: ChinaSymbol | None = None,
    filter_to_symbol: bool = False,
) -> str:
    try:
        df = fetch()
    except Exception as exc:
        return _render_unavailable(title, source, f"{type(exc).__name__}: {exc}")

    if filter_to_symbol and symbol is not None:
        df = _filter_symbol_rows(df, symbol)
        if _empty(df):
            return _render_unavailable(
                title,
                source,
                f"{symbol.canonical} was not present in the current ranking snapshot.",
            )

    return _format_frame(title, source, df, limit=limit)


def _eastmoney_sections(ak, symbol: ChinaSymbol, limit: int) -> list[str]:
    em_symbol = symbol.prefixed_code
    return [
        _render_endpoint(
            title=f"东方财富股吧人气榜-最新排名 for {symbol.canonical}",
            source="stock_hot_rank_latest_em",
            fetch=lambda: _call_ak(ak, "stock_hot_rank_latest_em", symbol=em_symbol),
            limit=limit,
        ),
        _render_endpoint(
            title=f"东方财富股吧人气榜-历史趋势及粉丝特征 for {symbol.canonical}",
            source="stock_hot_rank_detail_em",
            fetch=lambda: _call_ak(ak, "stock_hot_rank_detail_em", symbol=em_symbol),
            limit=limit,
        ),
        _render_endpoint(
            title=f"东方财富股吧人气榜-热门关键词 for {symbol.canonical}",
            source="stock_hot_keyword_em",
            fetch=lambda: _call_ak(ak, "stock_hot_keyword_em", symbol=em_symbol),
            limit=limit,
        ),
        _render_endpoint(
            title=f"东方财富股吧人气榜-当前榜单命中 for {symbol.canonical}",
            source="stock_hot_rank_em",
            fetch=lambda: _call_ak(ak, "stock_hot_rank_em"),
            limit=limit,
            symbol=symbol,
            filter_to_symbol=True,
        ),
    ]


def _xueqiu_sections(ak, symbol: ChinaSymbol, limit: int) -> list[str]:
    return [
        _render_endpoint(
            title=f"雪球讨论热度榜命中 for {symbol.canonical}",
            source='stock_hot_tweet_xq(symbol="最热门")',
            fetch=lambda: _call_ak(ak, "stock_hot_tweet_xq", symbol="最热门"),
            limit=limit,
            symbol=symbol,
            filter_to_symbol=True,
        ),
    ]


def _tonghuashun_sections(ak, symbol: ChinaSymbol, limit: int) -> list[str]:
    endpoints = [
        ("同花顺量价齐升榜命中", "stock_rank_ljqs_ths"),
        ("同花顺量价齐跌榜命中", "stock_rank_ljqd_ths"),
    ]
    return [
        _render_endpoint(
            title=f"{title} for {symbol.canonical}",
            source=source,
            fetch=lambda source=source: _call_ak(ak, source),
            limit=limit,
            symbol=symbol,
            filter_to_symbol=True,
        )
        for title, source in endpoints
    ]


def fetch_china_social_sentiment(
    ticker: str,
    start_date: str,
    end_date: str,
    limit: int = _DEFAULT_LIMIT,
) -> str:
    """Return China-only sentiment/heat blocks for an A-share ticker.

    Sources are limited to the user's requested set: 东方财富、雪球、同花顺.
    The current AKShare surface provides Eastmoney Guba heat/ranking details,
    Xueqiu heat rankings, and Tonghuashun ranking/technical heat signals.
    """
    symbol = parse_china_symbol(ticker)
    if symbol is None:
        return f"DATA_UNAVAILABLE: {ticker} is not a Mainland China A-share symbol."

    try:
        ak = _akshare()
    except Exception as exc:
        return f"DATA_UNAVAILABLE: AKShare unavailable for China social sources: {type(exc).__name__}: {exc}"

    safe_limit = max(1, int(limit))
    sections = [
        f"## 中国大陆股市社交情绪/热度源 for {symbol.canonical}",
        f"Period requested: {start_date} to {end_date}",
        "Sources limited to: 东方财富、雪球、同花顺.",
        "Note: 当前 AKShare 的同花顺接口提供排行/技术热度信号，不提供评论区原帖。",
        "",
        "### 东方财富",
        *_eastmoney_sections(ak, symbol, safe_limit),
        "### 雪球",
        *_xueqiu_sections(ak, symbol, safe_limit),
        "### 同花顺",
        *_tonghuashun_sections(ak, symbol, safe_limit),
    ]
    return "\n\n".join(sections)

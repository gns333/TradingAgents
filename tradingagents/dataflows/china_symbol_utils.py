"""Helpers for Mainland China A-share symbol formats.

Different vendors want different spellings for the same instrument:

    user input      canonical      AKShare     Tushare      Yahoo
    -----------     ---------      -------     -------      -----
    600519.SH       600519.SH      600519      600519.SH    600519.SS
    600519.SS       600519.SH      600519      600519.SH    600519.SS
    sh600519        600519.SH      600519      600519.SH    600519.SS
    000001          000001.SZ      000001      000001.SZ    000001.SZ

The parser is syntactic and does not call any remote service, so it is safe to
run before every vendor request.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_SUFFIX_ALIASES = {
    "SH": "SH",
    "SS": "SH",
    "SSE": "SH",
    "SZ": "SZ",
    "SZSE": "SZ",
    "BJ": "BJ",
    "BSE": "BJ",
}

_BARE_A_SHARE = re.compile(r"^\d{6}$")
_SUFFIXED_A_SHARE = re.compile(r"^(?P<code>\d{6})\.(?P<exchange>[A-Za-z]+)$")
_PREFIXED_A_SHARE = re.compile(r"^(?P<exchange>sh|sz|bj)(?P<code>\d{6})$", re.I)


@dataclass(frozen=True)
class ChinaSymbol:
    """A normalized Mainland China equity symbol."""

    code: str
    exchange: str

    @property
    def canonical(self) -> str:
        return f"{self.code}.{self.exchange}"

    @property
    def akshare_code(self) -> str:
        return self.code

    @property
    def tushare_code(self) -> str:
        return self.canonical

    @property
    def prefixed_code(self) -> str:
        return f"{self.exchange}{self.code}"

    @property
    def yahoo_code(self) -> str:
        suffix = "SS" if self.exchange == "SH" else self.exchange
        return f"{self.code}.{suffix}"


def infer_exchange(code: str) -> str | None:
    """Infer the Chinese exchange from a six-digit stock code."""
    if not _BARE_A_SHARE.fullmatch(code):
        return None
    if code.startswith(("4", "8")):
        return "BJ"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    if code.startswith(("5", "6", "9")):
        return "SH"
    return None


def parse_china_symbol(raw: str) -> ChinaSymbol | None:
    """Return normalized A-share symbol metadata, or None for non-A-share input."""
    if not isinstance(raw, str):
        return None

    text = raw.strip().upper()
    if not text:
        return None

    prefixed = _PREFIXED_A_SHARE.fullmatch(text)
    if prefixed:
        return ChinaSymbol(
            code=prefixed.group("code"),
            exchange=_SUFFIX_ALIASES[prefixed.group("exchange").upper()],
        )

    suffixed = _SUFFIXED_A_SHARE.fullmatch(text)
    if suffixed:
        code = suffixed.group("code")
        exchange = infer_exchange(code) or _SUFFIX_ALIASES.get(suffixed.group("exchange").upper())
        if exchange is None:
            return None
        return ChinaSymbol(code=code, exchange=exchange)

    if _BARE_A_SHARE.fullmatch(text):
        exchange = infer_exchange(text)
        if exchange is not None:
            return ChinaSymbol(code=text, exchange=exchange)

    return None

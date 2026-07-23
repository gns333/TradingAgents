"""Helpers for Hong Kong stock symbols used by AKShare and Yahoo Finance."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SUFFIXED_HK = re.compile(r"^(?P<code>\d{1,5})\.HK$", re.I)


@dataclass(frozen=True)
class HongKongSymbol:
    """A normalized Hong Kong equity symbol with vendor-specific spellings."""

    numeric_code: str

    @property
    def akshare_code(self) -> str:
        return self.numeric_code.zfill(5)

    @property
    def yahoo_numeric_code(self) -> str:
        if len(self.numeric_code) <= 4:
            return self.numeric_code.zfill(4)
        return self.numeric_code

    @property
    def canonical(self) -> str:
        return f"{self.yahoo_numeric_code}.HK"

    @property
    def yahoo_code(self) -> str:
        return self.canonical


def parse_hong_kong_symbol(raw: str) -> HongKongSymbol | None:
    """Return normalized Hong Kong symbol metadata, or ``None`` otherwise.

    An explicit ``.HK`` suffix is required so bare A-share codes remain
    unambiguous. AKShare uses five digits (``00700``), while Yahoo commonly
    uses four for legacy codes (``0700.HK``).
    """
    if not isinstance(raw, str):
        return None

    matched = _SUFFIXED_HK.fullmatch(raw.strip())
    if matched is None:
        return None

    numeric = matched.group("code").lstrip("0")
    if not numeric:
        return None
    return HongKongSymbol(numeric_code=numeric)

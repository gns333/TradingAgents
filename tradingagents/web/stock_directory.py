"""China-stock directory for the web workbench autocomplete.

The directory powers search by code or name on the analysis form:

* A curated Mainland A-share seed works offline.
* AKShare A-share and Hong Kong snapshots are cached independently on disk and
  merged into one searchable directory when the optional dependency is present.

Lookups stay purely local on the request hot path; remote refresh happens only
when the lazy directory is first populated and its weekly cache is stale.
"""

from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..dataflows.china_symbol_utils import infer_exchange, parse_china_symbol
from ..dataflows.hong_kong_symbol_utils import parse_hong_kong_symbol

_CACHE_DIR = Path.home() / ".tradingagents" / "cache"
_CACHE_FILE = _CACHE_DIR / "a_share_directory.json"
_HK_CACHE_FILE = _CACHE_DIR / "hong_kong_stock_directory.json"
_CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # refresh AKShare snapshots weekly


# A compact, hand-maintained seed covering the most frequently analysed A-share
# names across the main indices and sectors. Codes are bare six-digit numbers;
# the exchange suffix is inferred so callers get canonical ``600519.SH`` forms.
_SEED_STOCKS: tuple[tuple[str, str], ...] = (
    ("600519", "贵州茅台"),
    ("000858", "五粮液"),
    ("600809", "山西汾酒"),
    ("000568", "泸州老窖"),
    ("002304", "洋河股份"),
    ("600600", "青岛啤酒"),
    ("600887", "伊利股份"),
    ("603288", "海天味业"),
    ("601318", "中国平安"),
    ("601288", "农业银行"),
    ("601398", "工商银行"),
    ("601939", "建设银行"),
    ("601988", "中国银行"),
    ("600036", "招商银行"),
    ("601166", "兴业银行"),
    ("600000", "浦发银行"),
    ("601328", "交通银行"),
    ("601668", "中国建筑"),
    ("601857", "中国石油"),
    ("600028", "中国石化"),
    ("601088", "中国神华"),
    ("600900", "长江电力"),
    ("601012", "隆基绿能"),
    ("300750", "宁德时代"),
    ("002594", "比亚迪"),
    ("601127", "赛力斯"),
    ("600104", "上汽集团"),
    ("000625", "长安汽车"),
    ("601633", "长城汽车"),
    ("002415", "海康威视"),
    ("000651", "格力电器"),
    ("000333", "美的集团"),
    ("600690", "海尔智家"),
    ("002230", "科大讯飞"),
    ("300059", "东方财富"),
    ("600030", "中信证券"),
    ("601688", "华泰证券"),
    ("300760", "迈瑞医疗"),
    ("600276", "恒瑞医药"),
    ("002821", "凯莱英"),
    ("300015", "爱尔眼科"),
    ("603259", "药明康德"),
    ("000538", "云南白药"),
    ("600436", "片仔癀"),
    ("002714", "牧原股份"),
    ("300498", "温氏股份"),
    ("600585", "海螺水泥"),
    ("601899", "紫金矿业"),
    ("603501", "韦尔股份"),
    ("688981", "中芯国际"),
    ("688111", "金山办公"),
    ("688041", "海光信息"),
    ("688256", "寒武纪"),
    ("002460", "赣锋锂业"),
    ("300274", "阳光电源"),
    ("002049", "紫光国微"),
    ("000725", "京东方A"),
    ("600745", "闻泰科技"),
    ("601138", "工业富联"),
    ("000001", "平安银行"),
    ("000002", "万科A"),
    ("600048", "保利发展"),
    ("601390", "中国中铁"),
    ("601186", "中国铁建"),
    ("600050", "中国联通"),
    ("600941", "中国移动"),
    ("601728", "中国电信"),
    ("603986", "兆易创新"),
    ("002475", "立讯精密"),
    ("300124", "汇川技术"),
    ("600570", "恒生电子"),
    ("601601", "中国太保"),
    ("601336", "新华保险"),
    ("601628", "中国人寿"),
    ("600031", "三一重工"),
    ("000100", "TCL科技"),
    ("002027", "分众传媒"),
    ("600009", "上海机场"),
    ("601111", "中国国航"),
    ("600115", "中国东航"),
    ("601919", "中远海控"),
)


@dataclass(frozen=True)
class StockEntry:
    """One Mainland China or Hong Kong instrument in the directory."""

    code: str  # canonical symbol, e.g. "600519.SH"
    name: str  # Chinese display name
    bare_code: str  # six-digit code, e.g. "600519"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "name": self.name, "bare_code": self.bare_code}


def _canonical_from_bare(bare_code: str) -> str | None:
    symbol = parse_china_symbol(bare_code)
    if symbol is not None:
        return symbol.canonical
    exchange = infer_exchange(bare_code)
    return f"{bare_code}.{exchange}" if exchange else None


def _build_seed_entries() -> dict[str, StockEntry]:
    entries: dict[str, StockEntry] = {}
    for bare_code, name in _SEED_STOCKS:
        canonical = _canonical_from_bare(bare_code)
        if canonical is None:
            continue
        entries[bare_code] = StockEntry(code=canonical, name=name, bare_code=bare_code)
    return entries


def _import_akshare() -> Any | None:
    try:
        return importlib.import_module("akshare")
    except Exception:  # noqa: BLE001 - AKShare is an optional extra
        return None


def _load_cached_entries(
    cache_file: Path,
    fetcher: Any,
    converter: Any,
) -> dict[str, StockEntry]:
    """Load a cached AKShare snapshot, refreshing it when stale."""
    try:
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < _CACHE_TTL_SECONDS:
                raw = json.loads(cache_file.read_text(encoding="utf-8"))
                return converter(raw)
    except (OSError, ValueError):
        pass

    snapshot = fetcher()
    if snapshot:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return converter(snapshot)
    return {}


def _load_cached_akshare_entries() -> dict[str, StockEntry]:
    return _load_cached_entries(
        _CACHE_FILE,
        _fetch_akshare_directory,
        _entries_from_raw,
    )


def _load_cached_hk_akshare_entries() -> dict[str, StockEntry]:
    return _load_cached_entries(
        _HK_CACHE_FILE,
        _fetch_akshare_hk_directory,
        _entries_from_hk_raw,
    )


def _entries_from_raw(raw: Any) -> dict[str, StockEntry]:
    entries: dict[str, StockEntry] = {}
    if not isinstance(raw, list):
        return entries
    for item in raw:
        if not isinstance(item, dict):
            continue
        bare_code = str(item.get("bare_code") or "").strip()
        name = str(item.get("name") or "").strip()
        canonical = _canonical_from_bare(bare_code)
        if bare_code and name and canonical:
            entries[bare_code] = StockEntry(code=canonical, name=name, bare_code=bare_code)
    return entries


def _entries_from_hk_raw(raw: Any) -> dict[str, StockEntry]:
    entries: dict[str, StockEntry] = {}
    if not isinstance(raw, list):
        return entries
    for item in raw:
        if not isinstance(item, dict):
            continue
        bare_code = str(item.get("bare_code") or "").strip()
        name = str(item.get("name") or "").strip()
        symbol = parse_hong_kong_symbol(f"{bare_code}.HK")
        if symbol is None or not name:
            continue
        akshare_code = symbol.akshare_code
        entries[f"HK:{akshare_code}"] = StockEntry(
            code=symbol.canonical,
            name=name,
            bare_code=akshare_code,
        )
    return entries


def _fetch_akshare_directory() -> list[dict[str, str]]:
    """Fetch the full A-share code/name table when AKShare is installed."""
    akshare = _import_akshare()
    if akshare is None:
        return []

    try:
        frame = akshare.stock_info_a_code_name()
    except Exception:  # noqa: BLE001 - network / upstream failures are non-fatal
        return []

    snapshot: list[dict[str, str]] = []
    try:
        for _, row in frame.iterrows():
            bare_code = str(row.get("code", "")).strip()
            name = str(row.get("name", "")).strip()
            if bare_code and name:
                snapshot.append({"bare_code": bare_code, "name": name})
    except Exception:  # noqa: BLE001 - defensive against schema drift
        return []
    return snapshot


def _fetch_akshare_hk_directory() -> list[dict[str, str]]:
    """Fetch and normalize the Hong Kong code/name table from AKShare."""
    akshare = _import_akshare()
    if akshare is None:
        return []

    try:
        frame = akshare.stock_hk_spot_em()
    except Exception:  # noqa: BLE001 - network / upstream failures are non-fatal
        return []

    snapshot: list[dict[str, str]] = []
    try:
        for _, row in frame.iterrows():
            raw_code = row.get("\u4ee3\u7801", row.get("code", ""))
            name = str(row.get("\u540d\u79f0", row.get("name", ""))).strip()
            symbol = parse_hong_kong_symbol(f"{str(raw_code).strip()}.HK")
            if symbol is not None and name:
                snapshot.append({"bare_code": symbol.akshare_code, "name": name})
    except Exception:  # noqa: BLE001 - defensive against schema drift
        return []
    return snapshot


class StockDirectory:
    """Searchable, lazily-populated China-stock directory."""

    def __init__(self) -> None:
        self._entries: dict[str, StockEntry] | None = None

    def _entries_map(self) -> dict[str, StockEntry]:
        if self._entries is None:
            merged = _build_seed_entries()
            # AKShare snapshots overlay/extend the offline Mainland seed.
            merged.update(_load_cached_akshare_entries())
            merged.update(_load_cached_hk_akshare_entries())
            self._entries = merged
        return self._entries

    def search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        """Return matches ranked by relevance for a code- or name-based query."""
        text = (query or "").strip()
        if not text:
            return []

        limit = max(1, min(int(limit or 10), 50))
        entries = self._entries_map()
        lowered = text.lower()

        code_prefix: list[StockEntry] = []
        code_contains: list[StockEntry] = []
        name_prefix: list[StockEntry] = []
        name_contains: list[StockEntry] = []

        for entry in entries.values():
            bare = entry.bare_code
            canonical_lower = entry.code.lower()
            if bare.startswith(text) or canonical_lower.startswith(lowered):
                code_prefix.append(entry)
            elif text in bare or lowered in canonical_lower:
                code_contains.append(entry)
            elif entry.name.startswith(text):
                name_prefix.append(entry)
            elif text in entry.name:
                name_contains.append(entry)

        ordered: list[StockEntry] = []
        seen: set[str] = set()
        for bucket in (code_prefix, name_prefix, code_contains, name_contains):
            for entry in sorted(bucket, key=lambda item: item.bare_code):
                if entry.code not in seen:
                    seen.add(entry.code)
                    ordered.append(entry)
        return [entry.as_dict() for entry in ordered[:limit]]


_DIRECTORY: StockDirectory | None = None


def get_stock_directory() -> StockDirectory:
    global _DIRECTORY
    if _DIRECTORY is None:
        _DIRECTORY = StockDirectory()
    return _DIRECTORY

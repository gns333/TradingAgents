from pathlib import Path

from fastapi.testclient import TestClient

from tradingagents.web import api
from tradingagents.web import stock_directory
from tradingagents.web.admin_store import AdminStore


def _client_with_store(tmp_path: Path, monkeypatch) -> TestClient:
    store = AdminStore(tmp_path / "admin.sqlite3")
    monkeypatch.setattr(api, "get_admin_store", lambda: store)
    return TestClient(api.create_app())


def test_stock_search_matches_code_and_name(monkeypatch):
    # Keep the directory hermetic: rely on the bundled seed only, no akshare.
    monkeypatch.setattr(stock_directory, "_load_cached_akshare_entries", lambda: {})
    monkeypatch.setattr(stock_directory, "_DIRECTORY", None)
    client = TestClient(api.create_app())

    by_code = client.get("/api/stocks/search", params={"q": "600519"}).json()
    assert by_code["items"], "code query should return matches"
    assert by_code["items"][0]["code"] == "600519.SH"

    by_name = client.get("/api/stocks/search", params={"q": "茅台"}).json()
    assert any(item["name"] == "贵州茅台" for item in by_name["items"])

    empty = client.get("/api/stocks/search", params={"q": ""}).json()
    assert empty["items"] == []


def test_stock_search_respects_limit(monkeypatch):
    monkeypatch.setattr(stock_directory, "_load_cached_akshare_entries", lambda: {})
    monkeypatch.setattr(stock_directory, "_DIRECTORY", None)
    client = TestClient(api.create_app())

    response = client.get("/api/stocks/search", params={"q": "6", "limit": 3})
    assert response.status_code == 200
    assert len(response.json()["items"]) <= 3


def test_report_history_save_list_get_delete(tmp_path: Path, monkeypatch):
    client = _client_with_store(tmp_path, monkeypatch)

    assert client.get("/api/reports").json()["items"] == []

    saved = api.get_admin_store().save_analysis_report(
        {
            "ticker": "600519.SH",
            "trade_date": "2026-07-10",
            "analysts": ["market", "news"],
            "sections": {
                "market_report": "# 市场\n看多",
                "final_trade_decision": "买入 600519",
                "empty_one": "   ",
            },
            "decision": "买入 600519",
        }
    )
    assert saved["id"]
    # Empty sections are dropped so the archive stays meaningful.
    assert "empty_one" not in saved["sections"]

    listing = client.get("/api/reports").json()["items"]
    assert len(listing) == 1
    assert listing[0]["ticker"] == "600519.SH"
    assert listing[0]["decision"] == "买入 600519"
    assert "sections" not in listing[0]
    assert "market_report" in listing[0]["section_keys"]

    detail = client.get(f"/api/reports/{saved['id']}").json()["item"]
    assert detail["sections"]["market_report"].startswith("# 市场")
    assert detail["analysts"] == ["market", "news"]

    assert client.delete(f"/api/reports/{saved['id']}").status_code == 200
    assert client.get("/api/reports").json()["items"] == []
    assert client.get(f"/api/reports/{saved['id']}").status_code == 404

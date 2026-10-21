from io import BytesIO
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from tradingagents.web import api, report_pdf
from tradingagents.web.admin_store import AdminStore


def example_report():
    return {
        "ticker": "600519.SH", "stock_name": "贵州茅台", "trade_date": "2026-10-21",
        "sections": {
            "market_report": "# 市场分析\n\n**中文报告**与 *盈利预测*。\n\n"
            "- 营业收入稳健\n- 现金流改善\n\n"
            "| 指标 | 2025 年报 | 2026 年报 |\n| --- | ---: | ---: |\n"
            "| 经营现金流 | 2.50 亿元 | 3.10 亿元 |\n\n"
            "> 数据仅用于示例。\n\n[原始数据](https://example.com/source)",
            "investment_debate_report": "## 看多研究员\n\n需求改善。\n\n## 看空研究员\n\n估值风险。",
            "final_trade_decision": "## 组合经理\n\n评级：持有\n\n控制仓位，关注现金流。",
        },
    }


@pytest.fixture
def cjk_font():
    try:
        return report_pdf._fonts()
    except report_pdf.PDFExportUnavailable as exc:
        pytest.skip(str(exc))


def test_pdf_contains_chinese_all_sections_and_embeds_font(cjk_font):
    result = report_pdf.render_report_pdf(example_report())
    assert result.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(result))
    text = "\n".join(page.extract_text() for page in reader.pages)
    for expected in ("贵州茅台", "市场分析", "经营现金流", "看多研究员", "看空研究员", "评级：持有"):
        assert expected in text
    assert len(reader.pages) >= 1
    assert "第 1 页" in text
    fonts = [font.get_object() for page in reader.pages for font in page["/Resources"]["/Font"].values()]
    assert any(font.get("/FontDescriptor", {}).get("/FontFile2") for font in fonts)


def test_pdf_selected_section_excludes_other_sections(cjk_font):
    reader = PdfReader(BytesIO(report_pdf.render_report_pdf(example_report(), "market_report")))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "经营现金流" in text
    assert "看多研究员" not in text
    assert "控制仓位" not in text


def test_pdf_long_tables_repeat_headers_and_split_large_cells(cjk_font):
    report = example_report()
    phrase = "长段内容用于验证跨页单元格。"
    rows = [f"| 样例项目{i} | {phrase * (450 if i == 0 else 3)} |" for i in range(50)]
    report["sections"] = {"fundamentals_report": "| 项目名称 | 说明内容 |\n| --- | --- |\n" + "\n".join(rows)}
    reader = PdfReader(BytesIO(report_pdf.render_report_pdf(report)))
    assert len(reader.pages) > 2
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "样例项目49" in text
    assert text.count("项目名称") >= 2
    assert "".join(text.split()).count(phrase) == 450 + 49 * 3
    for index, page in enumerate(reader.pages, start=1):
        assert f"第 {index} 页" in page.extract_text()


def test_pdf_wide_table_keeps_every_column_in_readable_groups(cjk_font):
    report = example_report()
    header = "|" + "|".join(f"指标{i}" for i in range(9)) + "|"
    report["sections"] = {"market_report": header + "\n|" + "---|" * 9 + "\n|" + "|".join(f"数据{i}" for i in range(9)) + "|"}
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(report_pdf.render_report_pdf(report))).pages)
    assert "宽表分组" in text
    for index in range(9):
        assert f"数据{index}" in text


def test_pdf_does_not_fetch_images_or_emit_unsafe_links(cjk_font, monkeypatch):
    import requests

    def no_network(*_args, **_kwargs):
        raise AssertionError("PDF must never fetch report-controlled URLs")

    monkeypatch.setattr(requests, "get", no_network)
    report = example_report()
    report["sections"] = {"market_report": "![图示](http://169.254.169.254/latest/meta-data/)\n\n"
                          "[危险](javascript:alert(1))\n\n<script>alert(1)</script>\n\n"
                          "1. 第一项\n   - 嵌套列表\n2. 第二项\n\n~~旧结论~~\n\n```text\n计算示例 123\n```"}
    reader = PdfReader(BytesIO(report_pdf.render_report_pdf(report)))
    assert "图示" in "\n".join(page.extract_text() for page in reader.pages)
    for page in reader.pages:
        for annotation in page.get("/Annots", []):
            assert "javascript:" not in str(annotation.get_object())


def test_pdf_rejects_empty_invalid_oversized_and_concurrent_exports():
    with pytest.raises(ValueError, match="当前章节"):
        report_pdf.render_report_pdf(example_report(), "missing")
    with pytest.raises(ValueError, match="尚无"):
        report_pdf.render_report_pdf({"sections": {}})
    with pytest.raises(report_pdf.PDFExportTooLarge):
        report_pdf.render_report_pdf({"sections": {"market_report": "x" * (report_pdf.MAX_REPORT_CHARS + 1)}})
    assert report_pdf._RENDER_SLOT.acquire(blocking=False)
    try:
        with pytest.raises(report_pdf.PDFExportBusy):
            report_pdf.render_report_pdf(example_report())
    finally:
        report_pdf._RENDER_SLOT.release()


def test_pdf_render_slot_released_after_failure(monkeypatch):
    def fail(*_args):
        raise ValueError("failure")

    monkeypatch.setattr(report_pdf, "_build_pdf", fail)
    with pytest.raises(ValueError, match="failure"):
        report_pdf.render_report_pdf(example_report())
    assert report_pdf._RENDER_SLOT.acquire(blocking=False)
    report_pdf._RENDER_SLOT.release()


def test_pdf_download_authorizes_owner_before_rendering_and_allows_admin(tmp_path, monkeypatch):
    store = AdminStore(tmp_path / "pdf.sqlite3")
    store.set_admin_password("correct-horse")
    a = store.save_analysis_report({**example_report(), "owner_key": "uid:a"})
    b = store.save_analysis_report({**example_report(), "owner_key": "uid:b"})
    calls = []

    def render(report, section):
        calls.append((report["id"], section))
        return b"%PDF-test"

    monkeypatch.setattr(api, "render_report_pdf", render)
    client = TestClient(api.create_app(store=store))
    headers = {"x-user-uid": "a", "x-user-email": "a@example.com"}
    assert client.get(f"/api/reports/{b['id']}/pdf", headers=headers).status_code == 404
    assert not calls
    assert client.get(f"/api/reports/{a['id']}/pdf").status_code == 401
    response = client.get(f"/api/reports/{a['id']}/pdf?section=market_report", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "贵州茅台" in unquote(response.headers["content-disposition"])
    assert calls == [(a["id"], "market_report")]
    token = store.create_admin_session()
    assert client.get(f"/api/reports/{b['id']}/pdf", headers={"Authorization": f"Bearer {token}"}).status_code == 200


@pytest.mark.parametrize("error,status", [
    (report_pdf.PDFExportBusy("busy"), 429),
    (report_pdf.PDFExportUnavailable("unavailable"), 503),
    (report_pdf.PDFExportTooLarge("too large"), 413),
    (ValueError("invalid section"), 400),
])
def test_pdf_api_reports_actionable_errors(tmp_path, monkeypatch, error, status):
    store = AdminStore(tmp_path / "pdf.sqlite3")
    saved = store.save_analysis_report(example_report())

    def fail(*_args):
        raise error

    monkeypatch.setattr(api, "render_report_pdf", fail)
    response = TestClient(api.create_app(store=store)).get(f"/api/reports/{saved['id']}/pdf")
    assert response.status_code == status
    assert response.json()["detail"] == str(error)
    if status == 429:
        assert response.headers["retry-after"] == "3"


def test_pdf_filename_cannot_inject_paths_or_headers():
    name = report_pdf.report_pdf_filename({"stock_name": '../../A\r\nX-Evil: yes', "ticker": '600519.SH'})
    assert not any(char in name for char in "/\\\r\n:")
    assert name.endswith(".pdf")


def test_pdf_api_returns_actual_selected_pdf(tmp_path, cjk_font):
    store = AdminStore(tmp_path / "pdf.sqlite3")
    saved = store.save_analysis_report(example_report())
    response = TestClient(api.create_app(store=store)).get(
        f"/api/reports/{saved['id']}/pdf", params={"section": "final_trade_decision"},
    )
    assert response.status_code == 200
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    assert "控制仓位" in text
    assert "现金流改善" not in text

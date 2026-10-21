"""Private, in-memory PDF export of stored reports; no network or browser engine."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore
from urllib.parse import urlsplit

SECTION_LABELS = {
    "market_report": "市场分析",
    "news_report": "新闻分析",
    "fundamentals_report": "基本面分析",
    "sentiment_report": "情绪分析",
    "investment_debate_report": "多空辩论",
    "investment_plan": "研究经理结论",
    "trader_investment_plan": "交易计划",
    "risk_debate_report": "风险辩论",
    "final_trade_decision": "组合经理最终决策",
}
MAX_REPORT_CHARS = 300_000
_RENDER_SLOT = BoundedSemaphore(1)


class PDFExportBusy(RuntimeError):
    """Keep PDF work from competing with the analysis worker without a bound."""


class PDFExportUnavailable(RuntimeError):
    """A dependency or embeddable Chinese font is missing."""


class PDFExportTooLarge(ValueError):
    """A report exceeds the bounded synchronous export size."""


def _text(value) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))


@lru_cache(maxsize=1)
def _fonts() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFError, TTFont

    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = (
        (Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"), None),
        (windows / "msyh.ttc", windows / "msyhbd.ttc"),
        (windows / "simsun.ttc", None),
        (Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"), None),
        (Path("/Library/Fonts/Arial Unicode.ttf"), None),
    )
    for regular, bold in candidates:
        if not regular.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont("ReportCJK", str(regular), subfontIndex=0))
            bold_name = "ReportCJK"
            if bold and bold.is_file():
                pdfmetrics.registerFont(TTFont("ReportCJKBold", str(bold), subfontIndex=0))
                bold_name = "ReportCJKBold"
            pdfmetrics.registerFontFamily(
                "ReportCJK", normal="ReportCJK", bold=bold_name,
                italic="ReportCJK", boldItalic=bold_name,
            )
            return "ReportCJK", bold_name
        except (TTFError, OSError):
            continue
    raise PDFExportUnavailable(
        "PDF 导出缺少可嵌入的中文字体；Linux 请安装 fonts-wqy-zenhei，"
        "Windows 请确认已安装微软雅黑或宋体。"
    )


def _safe_link(value: str) -> bool:
    if re.search(r"[\x00-\x20]", value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return (parts.scheme in {"http", "https"} and bool(parts.netloc)) or (
        parts.scheme == "mailto" and bool(parts.path)
    )


def _inline(tokens) -> str:
    result = []
    links = []
    for token in tokens or []:
        kind = token.type
        if kind in {"text", "html_inline", "code_inline"}:
            result.append(escape(_text(token.content)))
        elif kind == "softbreak":
            result.append(" ")
        elif kind == "hardbreak":
            result.append("<br/>")
        elif kind in {"strong_open", "strong_close", "em_open", "em_close"}:
            tag = "b" if kind.startswith("strong") else "i"
            result.append(f"<{'/' if kind.endswith('close') else ''}{tag}>")
        elif kind == "s_open":
            result.append("<strike>")
        elif kind == "s_close":
            result.append("</strike>")
        elif kind == "link_open":
            href = token.attrGet("href") or ""
            allowed = _safe_link(href)
            links.append(allowed)
            if allowed:
                result.append(f'<link href="{escape(href, quote=True)}" color="#2456a6">')
        elif kind == "link_close":
            if links and links.pop():
                result.append("</link>")
        elif kind == "image":
            # Never fetch report-controlled URLs (including private metadata
            # endpoints). Preserve the caption instead of making network calls.
            result.append(escape(f"[图片：{token.content or '未提供说明'}]"))
        elif token.children:
            result.append(_inline(token.children))
    return "".join(result) or "&#160;"


class _MarkdownPDF:
    def __init__(self, width: float, regular: str, bold: str):
        from reportlab.lib.styles import ParagraphStyle

        self.width = width
        self.styles = {
            "body": ParagraphStyle(
                "Body", fontName=regular, fontSize=10, leading=16,
                wordWrap="CJK", spaceAfter=8, splitLongWords=True,
            ),
            "cell": ParagraphStyle(
                "Cell", fontName=regular, fontSize=8.5, leading=13,
                wordWrap="CJK", splitLongWords=True,
            ),
            "cell_heading": ParagraphStyle(
                "CellHeading", fontName=bold, fontSize=8.5, leading=13,
                wordWrap="CJK", splitLongWords=True, textColor="#17283e",
            ),
        }
        for level in range(1, 7):
            self.styles[f"h{level}"] = ParagraphStyle(
                f"Heading{level}", parent=self.styles["body"], fontName=bold,
                fontSize=max(11, 18 - level * 1.5), leading=max(17, 24 - level),
                spaceBefore=14, spaceAfter=8, keepWithNext=True,
                textColor="#17283e",
            )

    def _table(self, rows):
        from reportlab.lib import colors
        from reportlab.platypus import LongTable, Paragraph, Spacer, TableStyle

        if not rows:
            return []
        columns = max(map(len, rows))
        if columns > 32 or sum(map(len, rows)) > 5_000:
            raise PDFExportTooLarge("报告表格过大，请选择单个章节导出")
        rows = [row + ["&#160;"] * (columns - len(row)) for row in rows]
        # Wide tables are continued in column groups, with the identifying
        # first column repeated. Do not shrink financial figures into tiny text.
        groups = [list(range(columns))] if columns <= 6 else [
            [0, *range(start, min(start + 5, columns))]
            for start in range(1, columns, 5)
        ]
        result = []
        for index, group in enumerate(groups):
            if len(groups) > 1:
                result.append(Paragraph(f"宽表分组 {index + 1}/{len(groups)}", self.styles["body"]))
            data = [[Paragraph(row[col], self.styles["cell_heading" if row_index == 0 else "cell"])
                     for col in group] for row_index, row in enumerate(rows)]
            weights = [min(18, max(6, max(
                len(re.sub(r"<[^>]*>", "", row[col])) for row in rows[:20]
            ))) for col in group]
            widths = [self.width * weight / sum(weights) for weight in weights]
            table = LongTable(
                data, colWidths=widths, repeatRows=1, splitByRow=1, splitInRow=1,
                hAlign="LEFT", spaceBefore=6, spaceAfter=10,
            )
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfd")]),
                ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            result.extend([table, Spacer(1, 4)])
        return result

    def render(self, markdown: str):
        from markdown_it import MarkdownIt
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import HRFlowable, Paragraph

        tokens = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"]).parse(markdown)
        result = []
        lists = []
        quote_depth = 0
        prefix = None
        index = 0
        while index < len(tokens):
            token = tokens[index]
            kind = token.type
            if kind == "table_open":
                rows, row = [], []
                index += 1
                while index < len(tokens) and tokens[index].type != "table_close":
                    cell = tokens[index]
                    if cell.type == "tr_open":
                        row = []
                    elif cell.type == "inline":
                        row.append(_inline(cell.children))
                    elif cell.type == "tr_close":
                        rows.append(row)
                    index += 1
                result.extend(self._table(rows))
            elif kind in {"bullet_list_open", "ordered_list_open"}:
                lists.append({"ordered": kind == "ordered_list_open", "next": int(token.attrGet("start") or 1)})
            elif kind in {"bullet_list_close", "ordered_list_close"}:
                lists.pop()
            elif kind == "list_item_open" and lists:
                current = lists[-1]
                prefix = f"{current['next']}." if current["ordered"] else "•"
                current["next"] += 1
            elif kind == "list_item_close":
                prefix = None
            elif kind == "blockquote_open":
                quote_depth += 1
            elif kind == "blockquote_close":
                quote_depth -= 1
            elif kind in {"heading_open", "paragraph_open"}:
                inline = tokens[index + 1]
                base = self.styles[token.tag] if kind == "heading_open" else self.styles["body"]
                indent = 14 * min(len(lists), 8) + 10 * min(quote_depth, 4)
                style = ParagraphStyle(
                    "Block", parent=base, leftIndent=indent, bulletIndent=max(0, indent - 12),
                    bulletFontName=base.fontName, bulletFontSize=base.fontSize,
                    textColor="#526174" if quote_depth else base.textColor,
                )
                result.append(Paragraph(_inline(inline.children), style, bulletText=prefix))
                prefix = None
                index += 2
            elif kind in {"fence", "code_block", "html_block"}:
                content = escape(_text(token.content)).replace("\n", "<br/>")
                result.append(Paragraph(content, self.styles["body"]))
            elif kind == "hr":
                result.append(HRFlowable(width="100%", thickness=.5, color="#cbd5e1", spaceAfter=10))
            index += 1
        return result


def report_pdf_filename(report: dict, section: str | None = None) -> str:
    scope = SECTION_LABELS.get(section, section) if section else "完整报告"
    name = "-".join(_text(value) for value in (
        report.get("stock_name"), report.get("ticker"), report.get("trade_date"), scope,
    ) if value)
    safe = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name)[:150].strip("._ ")
    return (safe or "report") + ".pdf"


def render_report_pdf(report: dict, section: str | None = None) -> bytes:
    sections = {key: value for key, value in report.get("sections", {}).items() if value is not None and str(value).strip()}
    if section and section not in sections:
        raise ValueError("当前章节不存在或没有可导出的内容")
    selected = [section] if section else [
        *[key for key in SECTION_LABELS if key in sections],
        *sorted(key for key in sections if key not in SECTION_LABELS),
    ]
    if not selected:
        raise ValueError("报告尚无可导出的内容")
    if sum(len(str(sections[key])) for key in selected) > MAX_REPORT_CHARS:
        raise PDFExportTooLarge("报告内容过长，请选择单个章节导出")
    if not _RENDER_SLOT.acquire(blocking=False):
        raise PDFExportBusy("另一个 PDF 正在生成，请稍后重试")
    try:
        try:
            return _build_pdf(report, sections, selected)
        except ImportError as exc:
            raise PDFExportUnavailable("PDF 组件未安装，请安装项目的 web 可选依赖") from exc
    finally:
        _RENDER_SLOT.release()


def _build_pdf(report: dict, sections: dict, selected: list[str]) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate
        from reportlab.platypus.doctemplate import LayoutError
    except ImportError as exc:
        raise PDFExportUnavailable("PDF 组件未安装，请安装项目的 web 可选依赖") from exc

    regular, bold = _fonts()
    output = BytesIO()
    title = " · ".join(filter(None, [_text(report.get("stock_name"))[:160], _text(report.get("ticker"))[:64]])) or "投资研究报告"

    class ReportDocument(SimpleDocTemplate):
        def afterPage(self):
            # Draw after table backgrounds, not underneath them on split pages.
            decorate(self.canv, self)

    doc = ReportDocument(
        output, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=20 * mm, bottomMargin=22 * mm,
        title=f"{title} - 投资研究报告", author="TradingAgents",
        pageCompression=1,
    )
    renderer = _MarkdownPDF(doc.width, regular, bold)
    heading = ParagraphStyle("Title", parent=renderer.styles["h1"], fontSize=21, leading=30)
    story = [
        Paragraph(escape(title), heading),
        Paragraph(f"分析日期：{escape(_text(report.get('trade_date')) or '未提供')}", renderer.styles["body"]),
        Paragraph("研究信息仅供参考，不构成投资建议。", renderer.styles["body"]),
        HRFlowable(width="100%", thickness=1, color="#b87400", spaceAfter=12),
    ]
    for key in selected:
        label = SECTION_LABELS.get(key, key)
        story.append(Paragraph(escape(label), renderer.styles["h1"]))
        markdown = _text(sections[key])
        first_heading = re.match(r"\s*#{1,6}\s+([^\n]+)\n?", markdown)
        if first_heading and first_heading[1].strip(" #*") == label:
            markdown = markdown[first_heading.end():]
        story.extend(renderer.render(markdown))

    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColorRGB(.36, .41, .48)
        canvas.drawString(document.leftMargin, A4[1] - 12 * mm, "多智能体投资研究报告")
        canvas.drawString(document.leftMargin, 12 * mm, "TradingAgents | 研究报告")
        canvas.drawRightString(A4[0] - document.rightMargin, 12 * mm, f"第 {document.page} 页")
        canvas.restoreState()

    try:
        doc.build(story)
    except LayoutError as exc:
        raise PDFExportTooLarge("当前报告包含超大排版块，请尝试单个章节导出") from exc
    return output.getvalue()

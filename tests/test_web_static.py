"""Static web UI behavior checks."""

from pathlib import Path
import unittest


class WebStaticTests(unittest.TestCase):
    def test_index_handles_run_failed_event(self):
        html = Path("tradingagents/web/static/index.html").read_text(encoding="utf-8")

        self.assertIn("run_failed", html)
        self.assertIn("error_type", html)
        self.assertIn("streamDone", html)

    def test_analysis_modules_are_checkboxes(self):
        html = Path("tradingagents/web/static/index.html").read_text(encoding="utf-8")

        self.assertIn('type="checkbox"', html)
        self.assertIn('value="market"', html)
        self.assertIn('value="news"', html)
        self.assertIn('value="fundamentals"', html)
        self.assertIn("getSelectedAnalysts", html)

    def test_reports_render_markdown(self):
        html = Path("tradingagents/web/static/index.html").read_text(encoding="utf-8")

        self.assertIn("function renderMarkdown", html)
        self.assertIn("article.className = 'report-article'", html)
        self.assertIn("article.querySelector('.markdown').innerHTML = renderMarkdown(content);", html)
        self.assertIn("<table>", html)

    def test_process_output_collapses_and_agent_status_is_visible(self):
        html = Path("tradingagents/web/static/index.html").read_text(encoding="utf-8")

        self.assertIn('id="run-state"', html)
        self.assertIn('id="current-agent"', html)
        self.assertIn('id="team-board"', html)
        self.assertIn("function addCollapsibleLog", html)
        self.assertIn("safeDetail.length > COLLAPSE_LIMIT", html)
        self.assertIn("document.createElement('details')", html)
        self.assertIn("团队协作", html)

    def test_team_roles_are_mapped_from_report_sections(self):
        html = Path("tradingagents/web/static/index.html").read_text(encoding="utf-8")

        self.assertIn("const TEAM_ROLES", html)
        self.assertIn("market_report", html)
        self.assertIn("final_trade_decision", html)
        self.assertIn("组合经理", html)

    def test_reports_are_grouped_into_tabs(self):
        html = Path("tradingagents/web/static/index.html").read_text(encoding="utf-8")

        self.assertIn('id="report-tabs"', html)
        self.assertIn('id="report-panels"', html)
        self.assertIn('role="tablist"', html)
        self.assertIn("function activateReportTab", html)
        self.assertIn("function ensureReportTab", html)
        self.assertIn("activateReportTab(section)", html)


if __name__ == "__main__":
    unittest.main()

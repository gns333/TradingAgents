"""Static web UI behavior checks for the split workbench assets."""

from pathlib import Path
import unittest


STATIC_DIR = Path("tradingagents/web/static")


class WebStaticTests(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    def test_index_handles_run_failed_event(self):
        self.assertIn("run_failed", self.js)
        self.assertIn("error_type", self.js)
        self.assertIn("streamDone", self.js)

    def test_analysis_modules_are_checkboxes(self):
        self.assertIn('type="checkbox"', self.js)
        self.assertIn('value="market"', self.js)
        self.assertIn('value="news"', self.js)
        self.assertIn('value="fundamentals"', self.js)
        self.assertIn("getSelectedAnalysts", self.js)

    def test_reports_render_markdown(self):
        self.assertIn("function renderMarkdown(markdown)", self.js)
        self.assertIn("panel.className = 'report-article markdown'", self.js)
        self.assertIn("const body = sectionsMap[section] || ''", self.js)
        self.assertIn("+ renderMarkdown(body)", self.js)
        self.assertIn("output.push('<table>')", self.js)

    def test_process_output_collapses_and_agent_status_is_visible(self):
        self.assertIn('id="global-run-state"', self.html)
        self.assertIn('id="current-agent"', self.js)
        self.assertIn('id="team-board"', self.js)
        self.assertIn("function addCollapsibleLog", self.js)
        self.assertIn("safeDetail.length > COLLAPSE_LIMIT", self.js)
        self.assertIn("document.createElement('details')", self.js)
        self.assertIn("function advancePipelineStage()", self.js)
        self.assertIn("位分析师并行处理中", self.js)
        self.assertIn("formatEventTime(createdAt)", self.js)

    def test_team_roles_are_mapped_from_report_sections(self):
        self.assertIn("const TEAM_ROLES", self.js)
        self.assertIn("market_report", self.js)
        self.assertIn("final_trade_decision", self.js)
        self.assertIn("组合经理", self.js)

    def test_reports_are_grouped_into_tabs(self):
        self.assertIn("function buildReportTabs", self.js)
        self.assertIn("tabs.className = 'report-tabs'", self.js)
        self.assertIn("tabs.setAttribute('role', 'tablist')", self.js)
        self.assertIn("tab.setAttribute('role', 'tab')", self.js)
        self.assertIn("tab.addEventListener('click', () => paint(section))", self.js)
        self.assertIn("buildReportTabs(root, state.reports", self.js)


if __name__ == "__main__":
    unittest.main()

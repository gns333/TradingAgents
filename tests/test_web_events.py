"""Web event serialization for streaming analysis runs."""

import json
import unittest

from tradingagents.web.events import AnalysisEvent, sse_encode


class WebEventTests(unittest.TestCase):
    def test_sse_encode_renders_event_name_and_json_payload(self):
        event = AnalysisEvent(
            event="tool_called",
            data={"tool": "get_stock_data", "args": {"symbol": "600519.SH"}},
        )

        encoded = sse_encode(event)

        self.assertTrue(encoded.startswith("event: tool_called\n"))
        self.assertTrue(encoded.endswith("\n\n"))
        payload = encoded.split("data: ", 1)[1].strip()
        self.assertEqual(
            json.loads(payload),
            {"event": "tool_called", "data": {"tool": "get_stock_data", "args": {"symbol": "600519.SH"}}},
        )

    def test_sse_encode_escapes_multiline_payloads_as_json(self):
        event = AnalysisEvent(event="report_section_updated", data={"content": "line 1\nline 2"})

        encoded = sse_encode(event)

        self.assertIn('line 1\\nline 2', encoded)
        self.assertNotIn("data: line 2", encoded)


if __name__ == "__main__":
    unittest.main()

"""Graph stream to web event conversion."""

import unittest

from tradingagents.web.runner import (
    AnalysisRequest,
    config_for_request,
    stream_analysis_events,
)


class FakeToolCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class FakeMessage:
    def __init__(self, content="", tool_calls=None, message_id=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.id = message_id


class FakePropagator:
    def __init__(self):
        self.initial_state = None

    def create_initial_state(self, ticker, trade_date, asset_type="stock", instrument_context=""):
        self.initial_state = {
            "ticker": ticker,
            "trade_date": trade_date,
            "asset_type": asset_type,
            "instrument_context": instrument_context,
        }
        return self.initial_state

    def get_graph_args(self, callbacks=None):
        return {"stream_mode": "values", "config": {"callbacks": callbacks or []}}


class FakeCompiledGraph:
    def stream(self, initial_state, **args):
        yield {
            "messages": [
                FakeMessage(
                    "Fetching market data",
                    [FakeToolCall("get_stock_data", {"symbol": initial_state["ticker"]})],
                    message_id="m1",
                )
            ],
            "market_report": "Market report body",
            "investment_debate_state": {
                "history": (
                    "\nBull Analyst: Demand remains strong."
                    "\nBear Analyst: Valuation leaves little margin of safety."
                )
            },
        }
        yield {
            "messages": [FakeMessage("Final decision ready", message_id="m2")],
            "risk_debate_state": {
                "history": (
                    "\nAggressive Analyst: Add exposure on strength."
                    "\nConservative Analyst: Keep the position small."
                    "\nNeutral Analyst: Scale in only after confirmation."
                )
            },
            "final_trade_decision": "Buy with risk controls",
        }


class FakeGraph:
    def __init__(self):
        self.propagator = FakePropagator()
        self.graph = FakeCompiledGraph()

    def resolve_instrument_context(self, ticker, asset_type):
        return f"Context for {ticker}/{asset_type}"


class FailingCompiledGraph:
    def stream(self, initial_state, **args):
        yield {"messages": [FakeMessage("Starting", message_id="m1")]}
        raise RuntimeError("provider failed")


class FailingGraph(FakeGraph):
    def __init__(self):
        self.propagator = FakePropagator()
        self.graph = FailingCompiledGraph()


class WebRunnerTests(unittest.TestCase):
    def test_a_share_request_uses_china_mainland_data_vendors(self):
        config = config_for_request(AnalysisRequest(ticker="600519.SH", trade_date="2026-07-11"))

        self.assertEqual(config["market_profile"], "china_mainland")
        self.assertEqual(
            config["data_vendors"]["core_stock_apis"],
            "akshare,baostock,yfinance",
        )
        self.assertEqual(
            config["data_vendors"]["technical_indicators"],
            "akshare,baostock,yfinance",
        )
        self.assertEqual(
            config["data_vendors"]["fundamental_data"],
            "akshare,baostock,yfinance",
        )

    def test_non_a_share_request_keeps_default_market_profile(self):
        config = config_for_request(AnalysisRequest(ticker="AAPL", trade_date="2026-07-11"))

        self.assertEqual(config["market_profile"], "default")
        self.assertEqual(config["data_vendors"]["core_stock_apis"], "yfinance")

    def test_hong_kong_request_prefers_akshare_with_yfinance_fallback(self):
        config = config_for_request(AnalysisRequest(ticker="0700.HK", trade_date="2026-07-11"))

        self.assertEqual(config["market_profile"], "hong_kong")
        self.assertEqual(
            config["data_vendors"]["core_stock_apis"],
            "akshare,yfinance",
        )
        self.assertEqual(
            config["data_vendors"]["technical_indicators"],
            "akshare,yfinance",
        )
        self.assertEqual(
            config["data_vendors"]["fundamental_data"],
            "akshare,yfinance",
        )
        self.assertEqual(config["data_vendors"]["news_data"], "akshare,yfinance")

    def test_stream_analysis_events_emits_progress_reports_and_completion(self):
        graph = FakeGraph()
        request = AnalysisRequest(
            ticker="600519.SH",
            trade_date="2026-07-03",
            asset_type="stock",
            analysts=("market", "news"),
        )

        events = list(stream_analysis_events(graph, request))

        self.assertEqual(events[0].event, "run_started")
        self.assertEqual(events[0].data["ticker"], "600519.SH")
        self.assertIn(
            ("tool_called", "get_stock_data"), [(e.event, e.data.get("tool")) for e in events]
        )
        self.assertIn(
            ("report_section_updated", "market_report"),
            [(e.event, e.data.get("section")) for e in events],
        )
        reports = {
            event.data["section"]: event.data["content"]
            for event in events
            if event.event == "report_section_updated"
        }
        self.assertIn("investment_debate_report", reports)
        self.assertIn("### 看多研究员", reports["investment_debate_report"])
        self.assertIn("### 看空研究员", reports["investment_debate_report"])
        self.assertIn("risk_debate_report", reports)
        self.assertIn("### 激进风险分析师", reports["risk_debate_report"])
        self.assertIn("### 保守风险分析师", reports["risk_debate_report"])
        self.assertIn("### 中性风险分析师", reports["risk_debate_report"])
        self.assertEqual(events[-1].event, "run_completed")
        self.assertEqual(
            events[-1].data["final_state"]["final_trade_decision"], "Buy with risk controls"
        )
        self.assertEqual(
            graph.propagator.initial_state["instrument_context"], "Context for 600519.SH/stock"
        )

    def test_stream_analysis_events_emits_run_failed_on_exception(self):
        request = AnalysisRequest(ticker="600519.SH", trade_date="2026-07-03")

        events = list(stream_analysis_events(FailingGraph(), request))

        self.assertEqual(events[-1].event, "run_failed")
        self.assertEqual(events[-1].data["error_type"], "RuntimeError")
        self.assertIn("provider failed", events[-1].data["message"])


if __name__ == "__main__":
    unittest.main()

from pathlib import Path

from tradingagents.agents.utils.agent_utils import get_report_format_instruction


REPORT_NODES = (
    "tradingagents/agents/analysts/market_analyst.py",
    "tradingagents/agents/analysts/news_analyst.py",
    "tradingagents/agents/analysts/fundamentals_analyst.py",
    "tradingagents/agents/analysts/sentiment_analyst.py",
    "tradingagents/agents/managers/research_manager.py",
    "tradingagents/agents/managers/portfolio_manager.py",
    "tradingagents/agents/trader/trader.py",
)


def test_report_format_instruction_requires_markdown_without_fixed_sections():
    instruction = get_report_format_instruction()

    assert "Markdown" in instruction
    assert "fixed section" not in instruction.lower()


def test_every_saved_report_node_applies_markdown_instruction():
    for file_name in REPORT_NODES:
        source = Path(file_name).read_text(encoding="utf-8")
        assert "get_report_format_instruction" in source, file_name
        assert "get_report_format_instruction()" in source, file_name

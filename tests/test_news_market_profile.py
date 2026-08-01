"""Market-profile-specific news analyst tools and instructions."""

from tradingagents.agents.analysts.news_analyst import (
    _news_system_message,
    _prediction_markets_enabled,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph


def test_china_news_prompt_uses_only_china_macro_aliases_and_sources():
    config = {
        "market_profile": "china_mainland",
        "data_vendors": {
            "macro_data": "akshare",
            "prediction_markets": "disabled",
        },
    }

    prompt = _news_system_message("company", config)

    assert "AKShare China macro series" in prompt
    assert "cpi, ppi, pmi, gdp, lpr, money_supply" in prompt
    assert "fed_funds_rate" not in prompt
    assert "get_prediction_markets" not in prompt
    assert "Prediction-market probabilities are disabled" in prompt
    assert "Never relabel AKShare output as FRED" in prompt
    assert _prediction_markets_enabled(config) is False


def test_china_news_tool_node_does_not_expose_prediction_markets():
    graph = object.__new__(TradingAgentsGraph)
    graph.config = {
        "data_vendors": {
            "prediction_markets": "disabled",
        }
    }

    tools = TradingAgentsGraph._create_tool_nodes(graph)["news"].tools_by_name

    assert "get_macro_indicators" in tools
    assert "get_prediction_markets" not in tools


def test_default_news_tool_node_keeps_prediction_markets():
    graph = object.__new__(TradingAgentsGraph)
    graph.config = {
        "data_vendors": {
            "prediction_markets": "polymarket",
        }
    }

    tools = TradingAgentsGraph._create_tool_nodes(graph)["news"].tools_by_name

    assert "get_prediction_markets" in tools

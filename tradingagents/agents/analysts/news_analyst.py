from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_global_news,
    get_instrument_context_from_state,
    get_language_instruction,
    get_report_format_instruction,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
)


_DISABLED_VENDORS = {"disabled", "none", "off"}


def _prediction_markets_enabled(config: dict | None) -> bool:
    vendor = str(
        (config or {}).get("data_vendors", {}).get("prediction_markets", "polymarket")
    ).strip().lower()
    return vendor not in _DISABLED_VENDORS


def _news_system_message(asset_label: str, config: dict | None) -> str:
    profile = str((config or {}).get("market_profile", "default"))
    source_rule = (
        "For every quantitative claim, name the source exactly as identified in "
        "the tool result. Never relabel AKShare output as FRED, or news as a "
        "macroeconomic time series. If a tool returns DATA_UNAVAILABLE, state the "
        "gap once and do not fabricate or estimate a replacement."
    )
    if profile == "china_mainland":
        return (
            "You are a news researcher analyzing a Mainland China security and "
            "China-relevant market conditions over the past week. Use "
            f"get_news(query, start_date, end_date) for {asset_label}-specific "
            "news, get_global_news(curr_date, look_back_days, limit) for China "
            "policy and market headlines, and get_macro_indicators(indicator, "
            "curr_date, look_back_days) for AKShare China macro series. Request "
            "only China-profile aliases: cpi, ppi, pmi, gdp, lpr, money_supply, "
            "and social_financing. Do not request indicators outside this list "
            "or any overseas-source series. Prediction-market probabilities are "
            "disabled for this profile; do not claim or infer market-implied probabilities. "
            + source_rule
        )
    return (
        "You are a news researcher tasked with analyzing recent news and trends "
        "over the past week. Use get_news(query, start_date, end_date) for "
        f"{asset_label}-specific or targeted news, get_global_news(curr_date, "
        "look_back_days, limit) for broader macroeconomic news, and "
        "get_macro_indicators(indicator, curr_date, look_back_days) for "
        "quantitative macro data from the configured source. "
        + (
            "Use get_prediction_markets(topic, limit) for live market-implied "
            "probabilities of forward-looking events. "
            if _prediction_markets_enabled(config)
            else ""
        )
        + source_rule
    )


def create_news_analyst(llm, config: dict | None = None):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = get_instrument_context_from_state(state)

        tools = [
            get_news,
            get_global_news,
            get_macro_indicators,
        ]
        if _prediction_markets_enabled(config):
            tools.append(get_prediction_markets)

        system_message = (
            _news_system_message(asset_label, config)
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_report_format_instruction()
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node

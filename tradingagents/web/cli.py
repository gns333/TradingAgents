"""Command-line launcher for the optional TradingAgents web app."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the TradingAgents web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "The TradingAgents web app requires optional dependencies. "
            "Install them with `pip install 'tradingagents[web]'`."
        ) from exc

    uvicorn.run(
        "tradingagents.web.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()

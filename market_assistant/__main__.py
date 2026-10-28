"""Command line: `python -m market_assistant "question"`, `python -m market_assistant` to chat,
or `python -m market_assistant app` to run the local app."""
from __future__ import annotations

import argparse
import json

from market_assistant.agent.pipeline import Assistant


def main() -> None:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "app":
        app_parser = argparse.ArgumentParser(prog="market_assistant app", description="Run as a local app on this computer.")
        app_parser.add_argument("--port", type=int, default=8765)
        app_parser.add_argument("--no-window", action="store_true", help="do not open a window")
        app_args = app_parser.parse_args(sys.argv[2:])
        from market_assistant.desktop.server import run

        run(port=app_args.port, open_window=not app_args.no_window)
        return
    parser = argparse.ArgumentParser(prog="market_assistant", description=__doc__)
    parser.add_argument("question", nargs="?")
    parser.add_argument("--json", action="store_true", help="print the full JSON result")
    args = parser.parse_args()
    bot = Assistant()
    mode = "LLM" if bot.llm else "offline (rule-based planner and writer)"

    if args.question:
        result = bot.ask(args.question)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) if args.json else result.answer)
        return

    print(f"Market assistant, {mode}. Ask in Arabic or English, Ctrl+C to quit.")
    session = None
    try:
        while True:
            question = input("\nyou> ").strip()
            if question:
                result = bot.ask(question, session)
                session = result.session_id
                print(f"\nassistant> {result.answer}")
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    main()

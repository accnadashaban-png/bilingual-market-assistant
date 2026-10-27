"""Command line: `python -m market_assistant "question"` or `python -m market_assistant` to chat."""
from __future__ import annotations

import argparse
import json

from .pipeline import Assistant


def main() -> None:
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

"""
main.py: Ghost Ops tick loop.

Step A1: read ticks from fixture files and decide with the plain rule.
Run:  python main.py
      python main.py fixtures/control-tick.json
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.rules import decide

load_dotenv()

DEFAULT_FIXTURES = ["fixtures/outbreak-level-5.json", "fixtures/control-tick.json"]


def load_ticks(path: str) -> list[dict]:
    """A fixture may hold one tick, a list of ticks, or {"ticks": [...]}."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "ticks" in data:
        return data["ticks"]
    return [data]


def handle_tick(tick: dict) -> dict:
    decision = decide(tick)
    print(f"\n[Tick {tick.get('tick_id')}] facility={tick.get('facility_id')}")
    for a in decision["assessments"]:
        flag = "HOT " if a["hot"] else "calm"
        people = "civilians at risk" if a["civilians_at_risk"] else "no civilians at risk"
        print(f"  {flag} {a['sector']:<24} threat={a['threat']}  {people}")
    print(f"  DECISION: {decision['action'].upper()}  ({decision['rationale']})")
    return decision


def main():
    paths = sys.argv[1:] or DEFAULT_FIXTURES
    for path in paths:
        print(f"\n=== {path} ===")
        for tick in load_ticks(path):
            handle_tick(tick)


if __name__ == "__main__":
    main()
"""
main.py: Ghost Ops tick loop.

  python main.py                  # fixtures only (safe, nothing submitted)
  python main.py --live           # live feed, submits every tick (counts!)
  python main.py --live --dry-run # live ticks, decisions queued, not submitted
  python main.py --replay         # submit everything queued in pending_submissions.jsonl
"""
import asyncio
import json
import sys
import time

from src.feed import QUEUE_FILE, SCENARIOS, FixtureFeed, LiveFeed
from src.rules import decide

MAX_TICKS_PER_SCENARIO = 200


def print_tick(tick: dict, decision: dict):
    print(f"\n[Tick {tick.get('tick_id')}] facility={tick.get('facility_id')}")
    for a in decision["assessments"]:
        flag = "HOT " if a["hot"] else "calm"
        people = "civilians at risk" if a["civilians_at_risk"] else "no civilians at risk"
        print(f"  {flag} {a['sector']:<24} threat={a['threat']}  {people}")
    print(f"  DECISION: {decision['action'].upper()}  ({decision['rationale']})")


def print_judgment(j: dict):
    if j.get("queued"):
        print("  queued (not submitted)")
        return
    dup = "  [duplicate: original judgment]" if j.get("duplicate") else ""
    print(f"  JUDGED: expected={j.get('expected_action')} score={j.get('score')} "
          f"false_alarm={j.get('false_alarm')}{dup}")


async def run_scenario(feed, scenario: str, dry_run: bool):
    print(f"\n===== scenario: {scenario} =====")
    seen = set()
    for _ in range(MAX_TICKS_PER_SCENARIO):
        tick, exhausted = await feed.get_tick(scenario)
        tick_id = tick.get("tick_id")
        if tick_id in seen:  # exhausted feed repeats the last tick
            break
        seen.add(tick_id)

        t0 = time.perf_counter()
        decision = decide(tick)
        print_tick(tick, decision)

        if dry_run:
            with QUEUE_FILE.open("a") as f:
                f.write(json.dumps({"tick_id": tick_id, "action": decision["action"]}) + "\n")
            judgment = {"queued": True}
        else:
            judgment = await feed.submit(tick_id, decision["action"])
        print_judgment(judgment)
        print(f"  latency {time.perf_counter() - t0:.2f}s")

        if exhausted:
            break


async def replay(feed):
    if not QUEUE_FILE.exists():
        print("Nothing queued.")
        return
    for line in QUEUE_FILE.read_text().splitlines():
        item = json.loads(line)
        print(f"\nReplaying tick {item['tick_id']} -> {item['action']}")
        print_judgment(await feed.submit(item["tick_id"], item["action"]))
    QUEUE_FILE.unlink()


async def main():
    live = "--live" in sys.argv or "--replay" in sys.argv
    dry_run = "--dry-run" in sys.argv

    if not live:
        print("Fixture mode (nothing is submitted). Use --live for the real feed.")
        async with FixtureFeed() as feed:
            for scenario in SCENARIOS:
                await run_scenario(feed, scenario, dry_run=True)
        QUEUE_FILE.unlink(missing_ok=True)  # fixture runs never need replaying
        return

    try:
        async with LiveFeed() as feed:
            if "--replay" in sys.argv:
                await replay(feed)
            else:
                for scenario in SCENARIOS:
                    await run_scenario(feed, scenario, dry_run)
            board = await feed.scoreboard()
            print("\n===== scoreboard =====")
            print(json.dumps(board, indent=2))
    except Exception as e:  # feed down: keep going on fixtures, queue decisions
        print(f"\nLive feed failed ({type(e).__name__}: {e}). Falling back to fixtures; "
              f"decisions are queued, run --replay when the feed is back.")
        async with FixtureFeed() as feed:
            for scenario in SCENARIOS:
                await run_scenario(feed, scenario, dry_run=True)


if __name__ == "__main__":
    asyncio.run(main())
"""
main.py: Watchtower incident tick loop.

  python main.py                  # fixtures only (safe, nothing submitted)
  python main.py --live           # live feed, submits every tick (counts!)
  python main.py --live --dry-run # live ticks, decisions queued, not submitted
  python main.py --replay         # submit everything queued in pending_submissions.jsonl

Add --crew to any mode to decide with the CrewAI swarm instead of the plain rule.
  python main.py --crew                     # swarm on fixtures, nothing submitted
  python main.py --crew --submit-fixtures   # swarm on fixture ticks, submit to LIVE scoreboard
"""
import asyncio
import json
import sys
import time

from src.feed import QUEUE_FILE, SCENARIOS, FixtureFeed, LiveFeed
from src.metrics import Metrics
from src.rules import decide

try:
    from src.events import emit          # UI events (Person C's bridge)
except Exception:
    def emit(event):  # noqa: E306
        pass

USE_CREW = "--crew" in sys.argv
METRICS = Metrics()


def ui(event: dict):
    try:
        emit(event)
    except Exception:
        pass


async def decide_tick(tick: dict) -> dict:
    if USE_CREW:
        from src.crew import run_tick
        return await run_tick(tick)
    d = decide(tick)
    d["source"] = "rule"
    return d

MAX_TICKS_PER_SCENARIO = 200


def print_tick(tick: dict, decision: dict):
    print(f"\n[Tick {tick.get('tick_id')}] facility={tick.get('facility_id')}")
    for a in decision["assessments"]:
        flag = "HOT " if a["hot"] else "calm"
        people = "civilians at risk" if a["civilians_at_risk"] else "no civilians at risk"
        print(f"  {flag} {a['sector']:<24} threat={a['threat']}  {people}")
    print(f"  DECISION: {decision['action'].upper()}  ({decision['rationale']})")
    print(f"  source: {decision.get('source', 'rule')}  agents: {decision.get('agents_used', 0)}"
          f"  tokens: {sum(decision.get('usage', {}).get(k, 0) for k in ('prompt_tokens', 'completion_tokens'))}")
    for sec, t in (decision.get("triage") or {}).items():
        sim = f" sim={t['similarity']}" if t.get("similarity") is not None else ""
        print(f"  triage {sec}: {'HOT' if t['hot'] else 'calm'} via {t['method']}{sim}")
    if decision.get("searches"):
        print(f"  You.com searches: {decision['searches']}  sources: {decision.get('lore_sources', [])[:3]}")
    if decision.get("alert"):
        print(f"  ALERT (Discord, not sent yet): {decision['alert']}")


def print_judgment(j: dict):
    if j.get("queued"):
        print("  queued (not submitted)")
        return
    dup = "  [duplicate: original judgment]" if j.get("duplicate") else ""
    print(f"  JUDGED: expected={j.get('expected_action')} score={j.get('score')} "
          f"false_alarm={j.get('false_alarm')}{dup}")


async def run_scenario(feed, scenario: str, dry_run: bool, submit_feed=None):
    print(f"\n===== scenario: {scenario} =====")
    seen = set()
    for _ in range(MAX_TICKS_PER_SCENARIO):
        tick, exhausted = await feed.get_tick(scenario)
        tick_id = tick.get("tick_id")
        if tick_id in seen:  # exhausted feed repeats the last tick
            break
        seen.add(tick_id)

        t0 = time.perf_counter()
        budget = tick.get("swarm_budget") or {}
        METRICS.start_tick(tick_id, len(tick.get("sector_reports") or []),
                           budget.get("max_active_agents", 8))
        decision = await decide_tick(tick)
        METRICS.add_usage(decision.get("usage"))
        METRICS.add_search(decision.get("searches", 0))
        print_tick(tick, decision)

        if dry_run:
            with QUEUE_FILE.open("a") as f:
                f.write(json.dumps({"tick_id": tick_id, "action": decision["action"]}) + "\n")
            judgment = {"queued": True}
        else:
            judgment = await (submit_feed or feed).submit(tick_id, decision["action"])
        print_judgment(judgment)
        print(f"  latency {time.perf_counter() - t0:.2f}s")

        if "score" in judgment:
            ui({"type": "score", "tick_id": tick_id, "expected_action": judgment.get("expected_action"),
                "score": judgment.get("score"), "false_alarm": judgment.get("false_alarm")})
        row = METRICS.end_tick(action=decision["action"], score=judgment.get("score"),
                               false_alarm=judgment.get("false_alarm"),
                               escalated=bool(decision.get("alert")),
                               agents_used=decision.get("agents_used", 0),
                               research_skipped=not decision.get("searches"),
                               source=decision.get("source", ""))
        ui({"type": "metrics", "tick_id": tick_id, "total_tokens": row["total_tokens"],
            "cost_usd": row["cost_usd"], "latency_s": row["latency_s"],
            "agents_used": row["agents_used"], "max_agents": row["max_agents"],
            "searches": row["searches"]})

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

    if "--submit-fixtures" in sys.argv:  # live ticks == fixtures; cursor already exhausted
        print("Fixture ticks -> LIVE scoreboard. Each tick counts once!")
        async with LiveFeed() as live_feed, FixtureFeed() as fx:
            for scenario in SCENARIOS:
                await run_scenario(fx, scenario, dry_run=False, submit_feed=live_feed)
            print("\n===== scoreboard =====")
            print(json.dumps(await live_feed.scoreboard(), indent=2))
        return

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


def finish():
    METRICS.summary()
    if METRICS.ticks:
        METRICS.save("metrics.json")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        finish()
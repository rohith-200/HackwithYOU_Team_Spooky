"""
main.py: Watchtower incident tick loop.

  python main.py                  # fixtures only (safe, nothing submitted)
  python main.py --live           # live feed, submits every tick (counts!)
  python main.py --live --dry-run # live ticks, decisions queued, not submitted
  python main.py --replay         # submit everything queued in pending_submissions.jsonl

Add --crew to any mode to decide with the CrewAI swarm instead of the plain rule.
  python main.py --crew                     # swarm on fixtures, nothing submitted
  python main.py --crew --submit-fixtures   # swarm on fixture ticks, submit to LIVE scoreboard

Alerts go to Discord (webhook) only on real submissions (--live or --submit-fixtures).
  --no-discord     skip alerts
  --send-alerts    also alert in fixture/dry runs (for testing)
  --test-discord   send one test message and exit
Failed posts are printed and saved to manual_alerts.txt for posting by hand.
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

# Discord dispatch via webhook (organizers switched from One to a direct webhook)
try:
    from src.tools.discord_webhook import post_alert
    _DISPATCH_IMPORT_ERROR = ""
except Exception as _e:
    _DISPATCH_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"

    async def post_alert(content: str) -> bool:  # noqa: E306
        return False

USE_CREW = "--crew" in sys.argv
SEND_ALERTS = "--no-discord" not in sys.argv
SEND_ALERTS_ALWAYS = "--send-alerts" in sys.argv   # also alert in fixture/dry runs (testing)
MANUAL_ALERTS = "manual_alerts.txt"
METRICS = Metrics()


async def send_alert(tick_id, alert: str) -> bool:
    """Post an alert via the Discord webhook; on failure, fall back to manual posting."""
    if _DISPATCH_IMPORT_ERROR:
        print(f"  dispatcher not available ({_DISPATCH_IMPORT_ERROR})")
    ok = await post_alert(alert)
    ui({"type": "dispatch", "tick_id": tick_id, "content": alert, "ok": ok, "manual": not ok})
    if ok:
        print("  DISCORD: posted to #loft-incident")
        return True
    with open(MANUAL_ALERTS, "a") as f:
        f.write(alert + "\n")
    bar = "!" * 70
    print(f"  {bar}\n  MANUAL FALLBACK: post this in #loft-incident now (saved to {MANUAL_ALERTS}):\n"
          f"  {alert}\n  {bar}")
    return False


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
        print(f"  ALERT: {decision['alert']}")


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

        # Discord escalation AFTER submitting (never slows the scoreboard update);
        # only when the two-key check produced an alert, and only on real submissions
        if decision.get("alert") and SEND_ALERTS and (not dry_run or SEND_ALERTS_ALWAYS):
            if await send_alert(tick_id, decision["alert"]):
                METRICS.add_one_action(1)

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

    if "--test-discord" in sys.argv:
        import os
        ok = await post_alert(f"{os.environ.get('TEAM_HANDLE', 'team-xx')} · test · Ghost Ops swarm online")
        print("Discord test:", "posted" if ok else "failed")
        return

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
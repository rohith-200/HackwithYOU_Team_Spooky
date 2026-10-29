"""Inspect one real scenario ticket without submitting or sending alerts.

Run from the repository root:
    python -m scripts.test_live_ticket
    python -m scripts.test_live_ticket --crew
"""
import argparse
import asyncio
from uuid import uuid4

from main import USE_CREW, decide_tick, emit_rule_events, print_tick, ui
from src.events import event_context
from src.feed import SCENARIOS, LiveFeed
from src.metrics import Metrics


async def inspect_ticket(feed, scenario: str) -> tuple[dict, dict]:
    tick, _exhausted = await feed.get_tick(scenario)
    if not isinstance(tick, dict) or "tick_id" not in tick:
        raise RuntimeError(f"The live feed returned no valid ticket for {scenario!r}")

    metrics = Metrics()
    budget = tick.get("swarm_budget") or {}
    # This entry point deliberately never enters main.py's submission/queue loop.
    with event_context(run_id=f"{uuid4().hex}:live-dry-run", run_mode="live-dry-run"):
        metrics.start_tick(tick["tick_id"], len(tick.get("sector_reports") or []),
                           budget.get("max_active_agents", 8))
        decision = await decide_tick(tick)
        metrics.add_usage(decision.get("usage"))
        metrics.add_search(decision.get("searches", 0))
        print_tick(tick, decision)
        if not USE_CREW or str(decision.get("source", "")).startswith("rule fallback"):
            emit_rule_events(tick, decision)

        if decision.get("alert"):
            ui({"type": "dispatch", "tick_id": tick["tick_id"],
                "facility_id": tick.get("facility_id"), "content": decision["alert"],
                "status": "drafted"})

        row = metrics.end_tick(
            action=decision["action"], agents_used=decision.get("agents_used", 0),
            escalated=bool(decision.get("alert")),
            research_skipped=not decision.get("searches"),
            source=decision.get("source", ""),
            # CrewAI's counters omit embeddings and some fallback/tool calls.
            usage_complete=not USE_CREW,
        )
        ui({"type": "metrics", "tick_id": tick["tick_id"],
            "facility_id": tick.get("facility_id"),
            **{key: row[key] for key in (
                "total_tokens", "cost_usd", "latency_s", "agents_used", "max_agents",
                "searches", "cost_complete", "llm_calls", "source", "within_budget",
                "research_skipped", "by_model",
            )},
            "totals": metrics.totals()})
    print("READ-ONLY TEST: ticket fetched and analyzed; no submission or alert was sent.")
    return tick, decision


async def main():
    parser = argparse.ArgumentParser(
        description="Fetch and analyze one ticket without submitting or sending alerts."
    )
    parser.add_argument("--scenario", choices=SCENARIOS, default=SCENARIOS[0])
    parser.add_argument("--crew", action="store_true",
                        help="use CrewAI (requires the project dependencies and Parasail key)")
    args = parser.parse_args()

    async with LiveFeed() as feed:
        await inspect_ticket(feed, args.scenario)


if __name__ == "__main__":
    asyncio.run(main())

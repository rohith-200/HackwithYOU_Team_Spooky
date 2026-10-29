import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import main
from scripts.test_live_ticket import inspect_ticket
from src.events import emit


class ReadOnlyFeed:
    def __init__(self):
        self.fetches = 0
        self.submissions = 0

    async def get_tick(self, scenario):
        self.fetches += 1
        self.scenario = scenario
        return {
            "tick_id": 88, "facility_id": "test", "sector_reports": [],
            "swarm_budget": {"max_active_agents": 6},
        }, False

    async def submit(self, *_args):
        self.submissions += 1
        raise AssertionError("read-only ticket inspection must never submit")


def decision_with(**fields):
    return {
        "action": "observe", "sector": None, "rationale": "test decision",
        "assessments": [], "source": "rule", **fields,
    }


class LiveTicketInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def inspect_safely(self, decision, *, use_crew=False):
        feed = ReadOnlyFeed()
        events = []

        def record_event(request, **_kwargs):
            events.append(json.loads(request.data))
            return MagicMock()

        async def analyze(tick):
            if use_crew:
                # Real CrewAI emits from inside the analysis coroutine.
                main.ui({"type": "tick_start", "tick_id": tick["tick_id"],
                         "facility_id": tick["facility_id"], "sectors": []})
                main.ui({"type": "decision", "tick_id": tick["tick_id"],
                         "action": decision["action"]})
            return decision

        with TemporaryDirectory() as folder:
            queue = Path(folder) / "pending_submissions.jsonl"
            previous = '{"tick_id": 4, "action": "contain"}\n'
            queue.write_text(previous, encoding="utf-8")
            alerts = Path(folder) / "manual_alerts.txt"
            with (patch.object(main, "USE_CREW", use_crew),
                  patch("scripts.test_live_ticket.USE_CREW", use_crew),
                  patch("scripts.test_live_ticket.decide_tick", new=AsyncMock(side_effect=analyze)),
                  patch("src.events.urlopen", side_effect=record_event),
                  patch.object(main, "QUEUE_FILE", queue),
                  patch("src.feed.QUEUE_FILE", queue),
                  patch.object(main, "MANUAL_ALERTS", str(alerts)),
                  patch.object(main, "send_alert", new_callable=AsyncMock) as send_alert,
                  patch.object(main, "post_alert", new_callable=AsyncMock) as post_alert,
                  patch.object(main, "run_scenario", new_callable=AsyncMock) as run_scenario,
                  patch.object(main, "replay", new_callable=AsyncMock) as replay):
                tick, actual = await inspect_ticket(feed, "outbreak-level-5")
                emit({"type": "outside-context"})

            self.assertEqual(queue.read_text(encoding="utf-8"), previous)
            self.assertFalse(alerts.exists())
            self.assertEqual([path.name for path in Path(folder).iterdir()], [queue.name])
            for guarded in (send_alert, post_alert, run_scenario, replay):
                guarded.assert_not_awaited()

        self.assertEqual(feed.fetches, 1)
        self.assertEqual(feed.submissions, 0)
        self.assertEqual(feed.scenario, "outbreak-level-5")
        self.assertEqual(tick["tick_id"], 88)
        self.assertIs(actual, decision)
        self.assertEqual(events.pop(), {"type": "outside-context"})
        self.assertFalse(any(event["type"] == "score" for event in events))
        self.assertTrue(all(event["run_mode"] == "live-dry-run" for event in events))
        self.assertEqual(len({event["run_id"] for event in events}), 1)
        self.assertTrue(events[0]["run_id"].endswith(":live-dry-run"))
        return events

    async def test_inspects_exactly_one_ticket_without_submitting(self):
        events = await self.inspect_safely(decision_with())
        self.assertEqual([event["type"] for event in events], ["tick_start", "decision", "metrics"])
        metrics = events[-1]
        self.assertEqual(metrics["total_tokens"], 0)
        self.assertEqual(metrics["cost_usd"], 0)
        self.assertTrue(metrics["cost_complete"])
        self.assertEqual(metrics["max_agents"], 6)
        self.assertIsNone(metrics["totals"]["avg_score"])
        self.assertEqual(metrics["totals"]["scored_ticks"], 0)
        self.assertEqual(metrics["totals"]["discord_posts"], 0)

    async def test_crew_usage_is_reported_with_draft_only_and_incomplete_cost(self):
        usage = {
            "prompt_tokens": 1000, "completion_tokens": 200, "requests": 5,
            "by_model": {
                "parasail-glm-53-flash": {
                    "prompt_tokens": 1000, "completion_tokens": 200, "requests": 5,
                },
            },
        }
        decision = decision_with(action="evacuate", sector="entrance", source="swarm",
                                 agents_used=5, searches=1, usage=usage,
                                 alert="Draft only, never sent")
        events = await self.inspect_safely(decision, use_crew=True)
        self.assertEqual([event["type"] for event in events],
                         ["tick_start", "decision", "dispatch", "metrics"])
        draft = events[-2]
        self.assertEqual(draft["status"], "drafted")
        self.assertNotIn("ok", draft)
        metrics = events[-1]
        self.assertEqual(metrics["total_tokens"], 1200)
        self.assertEqual(metrics["llm_calls"], 5)
        self.assertEqual(metrics["searches"], 1)
        self.assertEqual(metrics["agents_used"], 5)
        self.assertEqual(metrics["max_agents"], 6)
        self.assertTrue(metrics["within_budget"])
        self.assertGreaterEqual(metrics["latency_s"], 0)
        self.assertAlmostEqual(metrics["cost_usd"], 0.00525)
        self.assertFalse(metrics["cost_complete"])
        self.assertEqual(metrics["by_model"], usage["by_model"])
        self.assertEqual(metrics["totals"]["total_tokens"], 1200)
        self.assertEqual(metrics["totals"]["discord_posts"], 0)
        self.assertEqual(metrics["totals"]["scored_ticks"], 0)

    async def test_repeated_inspections_have_distinct_runs_and_local_totals(self):
        first = await self.inspect_safely(decision_with())
        second = await self.inspect_safely(decision_with())
        self.assertNotEqual(first[0]["run_id"], second[0]["run_id"])
        self.assertEqual(first[-1]["totals"]["ticks"], 1)
        self.assertEqual(second[-1]["totals"]["ticks"], 1)

    async def test_invalid_ticket_never_starts_analysis_or_emits(self):
        feed = ReadOnlyFeed()
        feed.get_tick = AsyncMock(return_value=({"error": "no ticket"}, False))
        with (patch("scripts.test_live_ticket.decide_tick", new_callable=AsyncMock) as analyze,
              patch("src.events.urlopen") as send):
            with self.assertRaisesRegex(RuntimeError, "no valid ticket"):
                await inspect_ticket(feed, "outbreak-level-5")
        analyze.assert_not_awaited()
        send.assert_not_called()
        self.assertEqual(feed.submissions, 0)


if __name__ == "__main__":
    unittest.main()

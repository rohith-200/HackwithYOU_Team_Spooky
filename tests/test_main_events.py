from contextlib import redirect_stdout
from io import StringIO
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import main
from src.metrics import Metrics


class OneTickFeed:
    def __init__(self, judgment):
        self.judgment = judgment
        self.calls = []

    async def get_tick(self, _scenario):
        self.calls.append("fetch")
        return {
            "tick_id": 142,
            "facility_id": "test-facility",
            "swarm_budget": {"max_active_agents": 4},
            "sector_reports": [{
                "sector": "catering_coffee_bar",
                "sensors": {
                    "emf_milligauss": 80,
                    "temp_c": 1,
                    "motion_events": 12,
                },
                "sighting": {"confidence": 0.9},
                "civilian_status": "4 pinned",
                "containment_state": "breached",
            }],
        }, True

    async def submit(self, _tick_id, _action):
        self.calls.append("submit")
        return self.judgment


class MainEventTests(unittest.IsolatedAsyncioTestCase):
    async def run_one_tick(self, judgment):
        events = []
        with (patch.object(main, "USE_CREW", False),
              patch.object(main, "METRICS", Metrics()),
              patch.object(main, "emit", side_effect=events.append)):
            await main.run_scenario(OneTickFeed(judgment), "fixture", dry_run=False)
        return events

    async def test_rule_decision_streams_lifecycle_and_does_not_invent_score(self):
        events = await self.run_one_tick({"queued": True})

        self.assertEqual([event["type"] for event in events],
                         ["tick_start", "decision", "metrics"])
        start, decision, metrics = events
        self.assertEqual(start["facility_id"], "test-facility")
        self.assertEqual(start["sector_reports"][0]["sector"], "catering_coffee_bar")
        self.assertTrue(start["sector_reports"][0]["hot"])
        self.assertEqual(start["swarm_budget"]["max_active_agents"], 4)
        self.assertEqual(decision["action"], "evacuate")
        self.assertEqual(decision["facility_id"], "test-facility")
        self.assertEqual(metrics["facility_id"], "test-facility")
        self.assertNotIn("score", metrics)
        self.assertNotIn("score", [event["type"] for event in events])

    async def test_score_event_contains_the_received_judgment_and_facility(self):
        events = await self.run_one_tick({
            "expected_action": "evacuate",
            "score": 1.0,
            "false_alarm": False,
        })

        score = next(event for event in events if event["type"] == "score")
        self.assertEqual(score, {
            "type": "score",
            "tick_id": 142,
            "facility_id": "test-facility",
            "expected_action": "evacuate",
            "score": 1.0,
            "false_alarm": False,
        })

    async def test_discord_draft_is_streamed_but_not_sent_without_explicit_opt_in(self):
        events = []
        feed = OneTickFeed({"score": 1.0})
        decision = {
            "action": "evacuate",
            "sector": "catering_coffee_bar",
            "rationale": "test alert",
            "assessments": [],
            "alert": "team · catering_coffee_bar · evacuate · test",
        }
        with (patch.object(main, "USE_CREW", False),
              patch.object(main, "SEND_DISCORD", False),
              patch.object(main, "METRICS", Metrics()),
              patch.object(main, "emit", side_effect=events.append),
              patch.object(main, "decide_tick", new=AsyncMock(return_value=decision)),
              patch.object(main, "send_alert", new=AsyncMock()) as send):
            await main.run_scenario(feed, "fixture", dry_run=False)

        send.assert_not_awaited()
        dispatch = next(event for event in events if event["type"] == "dispatch")
        self.assertEqual(dispatch["status"], "drafted")
        self.assertNotIn("ok", dispatch)

    async def test_discord_opt_in_sends_only_after_submission(self):
        events = []
        feed = OneTickFeed({"score": 1.0})
        decision = {
            "action": "evacuate",
            "sector": "catering_coffee_bar",
            "rationale": "test alert",
            "assessments": [],
            "alert": "team · catering_coffee_bar · evacuate · test",
        }
        call_order = []

        async def send(_tick, _content):
            call_order.append("discord")
            return True

        with (patch.object(main, "USE_CREW", False),
              patch.object(main, "SEND_DISCORD", True),
              patch.object(main, "METRICS", Metrics()),
              patch.object(main, "emit", side_effect=events.append),
              patch.object(main, "decide_tick", new=AsyncMock(return_value=decision)),
              patch.object(main, "send_alert", side_effect=send) as send_mock):
            await main.run_scenario(feed, "fixture", dry_run=False)
            call_order = feed.calls + call_order

        send_mock.assert_awaited_once()
        self.assertEqual(call_order, ["fetch", "submit", "discord"])

    async def test_discord_opt_in_never_sends_for_dry_run_or_duplicate(self):
        decision = {
            "action": "evacuate",
            "sector": "catering_coffee_bar",
            "rationale": "test alert",
            "assessments": [],
            "alert": "team · catering_coffee_bar · evacuate · test",
        }
        with TemporaryDirectory() as directory:
            for dry_run, judgment in (
                (True, {}),
                (False, {"duplicate": True, "score": 1.0}),
            ):
                with self.subTest(dry_run=dry_run, judgment=judgment):
                    feed = OneTickFeed(judgment)
                    with (patch.object(main, "USE_CREW", False),
                          patch.object(main, "SEND_DISCORD", True),
                          patch.object(main, "METRICS", Metrics()),
                          patch.object(main, "QUEUE_FILE", Path(directory) / "pending.jsonl"),
                          patch.object(main, "decide_tick", new=AsyncMock(return_value=decision)),
                          patch.object(main, "send_alert", new=AsyncMock()) as send):
                        await main.run_scenario(feed, "fixture", dry_run=dry_run)
                    send.assert_not_awaited()

    async def test_discord_test_command_posts_without_opening_a_feed(self):
        with (patch.object(main, "post_alert", new=AsyncMock(return_value=True)) as send,
              patch.object(main, "LiveFeed") as feed,
              patch("sys.argv", ["main.py", "--test-discord"])):
            with redirect_stdout(StringIO()):
                await main.main()

        send.assert_awaited_once()
        feed.assert_not_called()


if __name__ == "__main__":
    unittest.main()

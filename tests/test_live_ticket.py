import unittest
from unittest.mock import AsyncMock, patch

import main
from scripts.test_live_ticket import inspect_ticket


class ReadOnlyFeed:
    def __init__(self):
        self.fetches = 0
        self.submissions = 0

    async def get_tick(self, scenario):
        self.fetches += 1
        self.scenario = scenario
        return {"tick_id": 88, "facility_id": "test", "sector_reports": []}, False

    async def submit(self, *_args):
        self.submissions += 1
        raise AssertionError("read-only ticket inspection must never submit")


class LiveTicketInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_inspects_exactly_one_ticket_without_submitting(self):
        feed = ReadOnlyFeed()
        decision = {
            "action": "observe",
            "sector": None,
            "rationale": "test decision",
            "assessments": [],
            "source": "rule",
        }

        with (patch.object(main, "USE_CREW", False),
              patch("scripts.test_live_ticket.USE_CREW", False),
              patch("scripts.test_live_ticket.decide_tick", new=AsyncMock(return_value=decision)),
              patch("scripts.test_live_ticket.emit_rule_events")):
            tick, actual_decision = await inspect_ticket(feed, "outbreak-level-5")

        self.assertEqual(feed.fetches, 1)
        self.assertEqual(feed.submissions, 0)
        self.assertEqual(feed.scenario, "outbreak-level-5")
        self.assertEqual(tick["tick_id"], 88)
        self.assertIs(actual_decision, decision)


if __name__ == "__main__":
    unittest.main()

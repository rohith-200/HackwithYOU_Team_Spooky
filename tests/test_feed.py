import unittest
from unittest.mock import AsyncMock

from src.feed import LiveFeed, _normalize_submission


class SubmissionNormalizationTests(unittest.TestCase):
    def test_official_nested_judgment_exposes_fields_consumed_by_main(self):
        judgment = {
            "tick_id": 142, "expected": "evacuate", "verdict": "correct",
            "score": 1, "false_alarm": False, "rationale": "Civilians at risk.",
            "expectation_source": "deterministic",
        }
        original = {"team": "team-spooky", "judgment": judgment,
                    "submitted_at": "2026-10-28T18:00:00Z", "duplicate": False}
        normalized = _normalize_submission(original)

        self.assertEqual(normalized["expected_action"], "evacuate")
        self.assertEqual(normalized["score"], 1)
        self.assertFalse(normalized["false_alarm"])
        self.assertEqual(normalized["tick_id"], 142)
        self.assertEqual(normalized["rationale"], judgment["rationale"])
        self.assertEqual(normalized["expectation_source"], "deterministic")
        self.assertEqual(normalized["team"], "team-spooky")
        self.assertEqual(normalized["submitted_at"], original["submitted_at"])
        self.assertIs(normalized["judgment"], judgment)
        self.assertNotIn("expected_action", original)
        self.assertNotIn("expected_action", judgment)

    def test_existing_flat_judgments_are_unchanged(self):
        original = {"tick_id": 900, "expected_action": "observe", "score": 0,
                    "false_alarm": True, "duplicate": False}
        self.assertEqual(_normalize_submission(original), original)

    def test_envelope_flags_override_conflicting_nested_flags(self):
        original = {
            "duplicate": True, "ok": False, "error": "already recorded",
            "judgment": {"expected": "contain", "score": 0.5,
                         "false_alarm": False, "duplicate": False,
                         "ok": True, "error": None},
        }
        normalized = _normalize_submission(original)
        self.assertTrue(normalized["duplicate"])
        self.assertFalse(normalized["ok"])
        self.assertEqual(normalized["error"], "already recorded")
        self.assertEqual(normalized["score"], 0.5)
        self.assertEqual(normalized["expected_action"], "contain")

    def test_error_or_ack_without_judgment_does_not_invent_a_score(self):
        for original in ({"ok": False, "error": "invalid tick"},
                         {"ok": True}, {"judgment": None},
                         {"judgment": "not a judgment"}):
            with self.subTest(original=original):
                normalized = _normalize_submission(original)
                self.assertEqual(normalized, original)
                self.assertNotIn("score", normalized)
                self.assertNotIn("expected_action", normalized)


class LiveFeedSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_normalizes_the_real_envelope_and_keeps_duplicate(self):
        feed = LiveFeed(team="test-team")
        feed._call = AsyncMock(return_value={
            "team": "test-team", "duplicate": True,
            "judgment": {"tick_id": 143, "expected": "evacuate", "score": 1,
                         "false_alarm": False},
        })
        actual = await feed.submit("143", "evacuate")

        feed._call.assert_awaited_once_with(
            "hack26-submit-containment",
            {"team": "test-team", "tick_id": 143, "action": "evacuate"},
        )
        self.assertEqual(actual["expected_action"], "evacuate")
        self.assertEqual(actual["score"], 1)
        self.assertTrue(actual["duplicate"])


if __name__ == "__main__":
    unittest.main()

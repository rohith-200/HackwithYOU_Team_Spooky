import unittest

from src.metrics import Metrics


class MetricsAccuracyTests(unittest.TestCase):
    def test_zero_agent_budget_is_enforced(self):
        metrics = Metrics()
        metrics.start_tick(1, sectors=1, max_agents=0)
        row = metrics.end_tick("observe", agents_used=1)
        self.assertFalse(row["within_budget"])
        self.assertEqual(metrics.totals()["budget_violations"], 1)

        metrics.start_tick(2, sectors=1, max_agents=0)
        self.assertTrue(metrics.end_tick("observe", agents_used=0)["within_budget"])

    def test_known_model_prices_do_not_hide_incomplete_usage(self):
        metrics = Metrics()
        metrics.start_tick(1, sectors=1, max_agents=4)
        usage = {"prompt_tokens": 1000, "completion_tokens": 500, "requests": 1}
        metrics.add_usage({**usage, "by_model": {"parasail-glm-53-flash": usage}})
        row = metrics.end_tick("observe", usage_complete=False)
        self.assertEqual(row["total_tokens"], 1500)
        self.assertEqual(row["cost_usd"], 0.0004)
        self.assertFalse(row["cost_complete"])
        self.assertFalse(metrics.totals()["cost_complete"])


if __name__ == "__main__":
    unittest.main()

import unittest

from core.guardrails import consume_per_task_budget


class GuardrailsSemanticsTest(unittest.TestCase):
    def test_per_run_budget_is_in_memory(self) -> None:
        counts: dict[str, int] = {}
        limit = 2
        self.assertTrue(consume_per_task_budget(counts, task_id="t1", limit=limit, cost=1))
        self.assertTrue(consume_per_task_budget(counts, task_id="t1", limit=limit, cost=1))
        self.assertFalse(consume_per_task_budget(counts, task_id="t1", limit=limit, cost=1))
        # Different task has independent budget.
        self.assertTrue(consume_per_task_budget(counts, task_id="t2", limit=limit, cost=2))
        self.assertFalse(consume_per_task_budget(counts, task_id="t2", limit=limit, cost=1))


if __name__ == "__main__":
    unittest.main()


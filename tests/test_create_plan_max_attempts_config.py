import json
import tempfile
import unittest
from pathlib import Path

from core.runtime_config import load_runtime_config


class CreatePlanMaxAttemptsConfigTest(unittest.TestCase):
    def test_loads_create_plan_max_attempts_with_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "runtime_config.json"
            p.write_text(json.dumps({"plan_review_pass_score": 90}, ensure_ascii=False), encoding="utf-8")
            cfg = load_runtime_config(p)
            self.assertEqual(int(cfg.create_plan_max_attempts), 3)

    def test_loads_create_plan_max_attempts_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "runtime_config.json"
            p.write_text(json.dumps({"create_plan_max_attempts": 7}, ensure_ascii=False), encoding="utf-8")
            cfg = load_runtime_config(p)
            self.assertEqual(int(cfg.create_plan_max_attempts), 7)


if __name__ == "__main__":
    unittest.main()


import json
import unittest
from pathlib import Path

from core.contracts_v2 import CONTRACT_SUMMARY, normalize_and_validate


class P02ContractSingleSourceTest(unittest.TestCase):
    def test_contract_summary_matches_doc(self) -> None:
        doc_path = Path(__file__).resolve().parents[1] / "doc" / "code" / "Contracts.md"
        text = doc_path.read_text(encoding="utf-8")
        start = "<!-- CONTRACT_SUMMARY_JSON_START -->"
        end = "<!-- CONTRACT_SUMMARY_JSON_END -->"
        self.assertIn(start, text)
        self.assertIn(end, text)
        payload = text.split(start, 1)[1].split(end, 1)[0].strip()
        doc_summary = json.loads(payload)
        self.assertEqual(doc_summary, CONTRACT_SUMMARY)

    def test_schema_mismatch_error_has_required_fields(self) -> None:
        bad_action = {
            "schema_version": "xiaobo_action_v1",
            "task_id": "t",
            "result_type": "ARTIFACT",
            "artifact": {"name": "index", "format": "exe", "content": "x"},
        }
        _, err = normalize_and_validate("TASK_ACTION", bad_action, {"task_id": "t"})
        self.assertIsNotNone(err)
        for k in ("error_code", "schema", "schema_version", "json_path", "expected", "actual", "example_fix"):
            self.assertIn(k, err)
            self.assertTrue(isinstance(err[k], str) and err[k].strip())
        self.assertEqual(err["json_path"], "$.artifact.format")

    def test_review_breakdown_mismatch_has_json_path_and_fix(self) -> None:
        bad_review = {
            "schema_version": "xiaojing_review_v1",
            "task_id": "t",
            "review_target": "NODE",
            "total_score": 10,
            "action_required": "MODIFY",
            "summary": "bad",
            "breakdown": [{"dimension": 1, "score": "x", "max_score": 100, "issues": []}],
            "suggestions": [{"priority": "MED", "change": "c", "steps": [], "acceptance_criteria": "a"}],
        }
        _, err = normalize_and_validate("TASK_CHECK", bad_review, {"task_id": "t"})
        self.assertIsNotNone(err)
        for k in ("json_path", "expected", "example_fix"):
            self.assertTrue(err.get(k))


if __name__ == "__main__":
    unittest.main()

import unittest

from core.contracts import normalize_plan_json, normalize_xiaojing_review, validate_xiaojing_review
from core.models import validate_plan_dict
from core.util import utc_now_iso


class ContractsTest(unittest.TestCase):
    def test_normalize_xiaobo_action_adds_schema_version(self) -> None:
        from core.contracts import normalize_xiaobo_action, validate_xiaobo_action

        raw = {"task_id": "t", "result_type": "NOOP"}
        norm = normalize_xiaobo_action(raw, task_id="t")
        ok, reason = validate_xiaobo_action(norm)
        self.assertTrue(ok, reason)

    def test_normalize_plan_accepts_id_from_to_aliases(self) -> None:
        raw = {
            "plan": {"id": "not-a-uuid", "name": "Test Plan", "root": "root"},
            "tasks": [
                {"id": "root", "type": "GOAL", "name": "Root", "owner": "xiaobo"},
                {"id": "t1", "type": "ACTION", "name": "Do", "owner": "xiaobo"},
            ],
            "links": [{"id": "e1", "from": "root", "to": "t1", "type": "DECOMPOSE", "meta": {"and_or": "AND"}}],
            "inputs": [],
        }
        norm = normalize_plan_json(raw, top_task="Top", utc_now_iso=utc_now_iso)
        validate_plan_dict(norm)
        self.assertGreaterEqual(len(norm["nodes"]), 2)
        self.assertGreaterEqual(len(norm["edges"]), 1)

    def test_normalize_plan_rewrites_start_end_chain(self) -> None:
        raw = {
            "plan": {"id": "p", "name": "Chain", "root": "ROOT"},
            "tasks": [{"id": "n1", "type": "ACTION", "name": "Do", "owner": "xiaobo"}],
            "links": [
                {"from": "START", "to": "n1", "type": "DEPENDS_ON"},
                {"from": "n1", "to": "END", "type": "DEPENDS_ON"},
            ],
        }
        norm = normalize_plan_json(raw, top_task="Top task", utc_now_iso=utc_now_iso)
        validate_plan_dict(norm)
        # Should not contain placeholder nodes for START/END after normalization.
        titles = [n.get("title") for n in norm["nodes"]]
        self.assertTrue(all(not (isinstance(t, str) and t.startswith("AUTO: missing node")) for t in titles))
        # Root should have a goal_statement.
        root_id = norm["plan"]["root_task_id"]
        root_node = next(n for n in norm["nodes"] if n["task_id"] == root_id)
        self.assertTrue(isinstance(root_node.get("goal_statement"), str) and root_node["goal_statement"])

    def test_normalize_review_accepts_review_result_wrapper(self) -> None:
        raw = {
            "schema_version": "xiaojing_review_v1",
            "review_target": "PLAN",
            "task_id": "t",
            "review_result": {
                "action_required": "MODIFY",
                "total_score": 10,
                "dimension_scores": [{"dimension": "Completeness", "score": 0, "comment": "bad"}],
                "suggestions": [{"problem": "p", "dimension": "Completeness", "change": "c", "steps": ["s1"], "acceptance_criteria": "a"}],
            },
        }
        norm = normalize_xiaojing_review(raw, task_id="t", review_target="PLAN")
        ok, reason = validate_xiaojing_review(norm, review_target="PLAN")
        self.assertTrue(ok, reason)
        self.assertEqual(norm["total_score"], 10)
        self.assertGreaterEqual(len(norm["suggestions"]), 1)
        self.assertGreaterEqual(len(norm["breakdown"]), 1)


if __name__ == "__main__":
    unittest.main()

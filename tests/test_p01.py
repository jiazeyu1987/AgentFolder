import sys
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.status_rules import NODE_TYPE_ALLOWED_STATUSES, validate_status_for_node_type  # noqa: E402


class P01StatusRulesTest(unittest.TestCase):
    def test_ready_to_check_only_allowed_for_action(self) -> None:
        validate_status_for_node_type(node_type="ACTION", status="READY_TO_CHECK")
        with self.assertRaises(Exception):
            validate_status_for_node_type(node_type="CHECK", status="READY_TO_CHECK")
        with self.assertRaises(Exception):
            validate_status_for_node_type(node_type="GOAL", status="READY_TO_CHECK")

    def test_doc_status_dictionary_matches_code(self) -> None:
        doc_path = Path(__file__).resolve().parents[1] / "doc" / "code" / "Glossary.md"
        text = doc_path.read_text(encoding="utf-8")
        start = "<!-- STATUS_RULES_JSON_START -->"
        end = "<!-- STATUS_RULES_JSON_END -->"
        self.assertIn(start, text)
        self.assertIn(end, text)
        payload = text.split(start, 1)[1].split(end, 1)[0].strip()
        doc_rules = json.loads(payload)

        self.assertIsInstance(doc_rules, dict)
        self.assertEqual(set(doc_rules.keys()), set(NODE_TYPE_ALLOWED_STATUSES.keys()))
        for node_type, allowed in NODE_TYPE_ALLOWED_STATUSES.items():
            self.assertEqual(set(doc_rules[node_type]), set(allowed), f"mismatch for {node_type}")


if __name__ == "__main__":
    unittest.main()

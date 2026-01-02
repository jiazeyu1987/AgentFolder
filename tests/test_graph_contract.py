import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.graph import build_plan_graph


class GraphContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_required_docs = config.REQUIRED_DOCS_DIR

    def tearDown(self) -> None:
        config.REQUIRED_DOCS_DIR = self._old_required_docs

    def test_required_docs_accepted_types_is_list(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            config.REQUIRED_DOCS_DIR = td_path / "required_docs"
            config.REQUIRED_DOCS_DIR.mkdir(parents=True, exist_ok=True)

            db_path = td_path / "t.db"
            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = "p"
                root_id = "p_root"
                conn.execute(
                    "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
                    (plan_id, root_id),
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, blocked_reason, created_at, updated_at) VALUES(?, ?, 'ACTION', 'NeedInput', 'xiaobo', 'BLOCKED', 'WAITING_INPUT', datetime('now'), datetime('now'))",
                    ("t1", plan_id),
                )
                conn.commit()

                req_path = config.REQUIRED_DOCS_DIR / "t1.md"
                req_path.write_text(
                    "\n".join(
                        [
                            "# Required Docs for t1",
                            "",
                            "- product_spec: What to build",
                            "  - accepted_types: ['md','txt','pdf']",
                            "  - suggested_path: workspace/inputs/product_spec/product_spec.md",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )

                res = build_plan_graph(conn, plan_id=plan_id)
                node = next(n for n in res.graph["nodes"] if n["task_id"] == "t1")
                missing = node.get("missing_inputs") or []
                self.assertTrue(missing)
                # required_docs takes precedence; accepted_types must be a list
                self.assertIsInstance(missing[0].get("accepted_types"), list)
                self.assertEqual(missing[0]["accepted_types"], ["md", "txt", "pdf"])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()


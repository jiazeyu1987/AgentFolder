import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.deliverables import export_deliverables


def _insert_plan(conn: sqlite3.Connection, plan_id: str = "p") -> str:
    root_id = f"{plan_id}_root"
    conn.execute(
        "INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json) VALUES(?, 'Plan', 'xiaobo', ?, datetime('now'), '{}')",
        (plan_id, root_id),
    )
    conn.execute(
        "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at) VALUES(?, ?, 'GOAL', 'Root', 'xiaobo', 'DONE', datetime('now'), datetime('now'))",
        (root_id, plan_id),
    )
    conn.commit()
    return plan_id


def _insert_done_action_with_artifacts(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    task_id: str,
    title: str,
    approved_path: Path,
    active_path: Path,
    tags: list[str] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO task_nodes(
          task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at,
          tags_json, approved_artifact_id, active_artifact_id
        )
        VALUES(?, ?, 'ACTION', ?, 'xiaobo', 'DONE', datetime('now'), datetime('now'), ?, ?, ?)
        """,
        (task_id, plan_id, title, json.dumps(tags or [], ensure_ascii=False), f"{task_id}_approved", f"{task_id}_active"),
    )
    conn.execute(
        "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES(?, ?, 'approved', ?, 'html', 1, 's1', datetime('now'))",
        (f"{task_id}_approved", task_id, str(approved_path)),
    )
    conn.execute(
        "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES(?, ?, 'active', ?, 'html', 2, 's2', datetime('now'))",
        (f"{task_id}_active", task_id, str(active_path)),
    )


class DeliverablesM4Test(unittest.TestCase):
    def test_export_approved_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            conn = connect(ws / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                approved = ws / "approved.html"
                active = ws / "active.html"
                approved.write_text("<html>approved</html>", encoding="utf-8")
                active.write_text("<html>active</html>", encoding="utf-8")
                _insert_done_action_with_artifacts(conn, plan_id=plan_id, task_id="a1", title="Final Package", approved_path=approved, active_path=active, tags=["final"])
                conn.commit()

                out_dir = ws / "deliverables"
                export_deliverables(conn, plan_id=plan_id, out_dir=out_dir, include_reviews=False, include_candidates=False)
                manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
                files = manifest.get("files") or []
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0]["artifact"]["artifact_id"], "a1_approved")
                self.assertIn("final.json", {p.name for p in out_dir.glob("*.json")})
            finally:
                conn.close()

    def test_final_json_points_to_existing_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            conn = connect(ws / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                approved = ws / "index.html"
                active = ws / "active.html"
                approved.write_text("<html>approved</html>", encoding="utf-8")
                active.write_text("<html>active</html>", encoding="utf-8")
                _insert_done_action_with_artifacts(conn, plan_id=plan_id, task_id="a1", title="Final Package", approved_path=approved, active_path=active, tags=["final"])
                conn.commit()

                out_dir = ws / "deliverables"
                export_deliverables(conn, plan_id=plan_id, out_dir=out_dir, include_reviews=False, include_candidates=False)
                final = json.loads((out_dir / "final.json").read_text(encoding="utf-8"))
                entry = Path(out_dir) / str(final["final_entrypoint"])
                self.assertTrue(entry.exists())
                self.assertTrue(str(final["final_entrypoint"]).lower().endswith(".html"))
            finally:
                conn.close()

    def test_export_fails_when_no_approved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            conn = connect(ws / "t.db")
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)
                plan_id = _insert_plan(conn, "p")
                # DONE ACTION but only active artifact (no approved pointer)
                conn.execute(
                    "INSERT INTO task_nodes(task_id, plan_id, node_type, title, owner_agent_id, status, created_at, updated_at, active_artifact_id) VALUES('a1', ?, 'ACTION', 'A1', 'xiaobo', 'DONE', datetime('now'), datetime('now'), 'art_active')",
                    (plan_id,),
                )
                active = ws / "active.html"
                active.write_text("<html>active</html>", encoding="utf-8")
                conn.execute(
                    "INSERT INTO artifacts(artifact_id, task_id, name, path, format, version, sha256, created_at) VALUES('art_active','a1','active',?, 'html', 1, 's', datetime('now'))",
                    (str(active),),
                )
                conn.commit()

                out_dir = ws / "deliverables"
                with self.assertRaises(Exception):
                    export_deliverables(conn, plan_id=plan_id, out_dir=out_dir, include_reviews=False, include_candidates=False)
            finally:
                conn.close()


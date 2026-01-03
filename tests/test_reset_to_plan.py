import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from core.db import apply_migrations, connect
from core.reset_to_plan import reset_plan_to_pre_run


class ResetToPlanTest(unittest.TestCase):
    def test_reset_to_pre_run_keeps_plan_and_clears_run_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws = root / "workspace"
            db_path = root / "state.db"

            conn = connect(db_path)
            try:
                apply_migrations(conn, config.MIGRATIONS_DIR)

                plan_id = "p1"
                root_task_id = "t_root"
                plan_check_id = "t_plan_check"
                action_id = "t_action"

                conn.execute(
                    "INSERT INTO plans(plan_id,title,owner_agent_id,root_task_id,created_at,constraints_json) VALUES(?,?,?,?,datetime('now'),?)",
                    (plan_id, "Plan", "xiaobo", root_task_id, "{}"),
                )
                conn.execute(
                    "INSERT INTO task_nodes(task_id,plan_id,node_type,title,owner_agent_id,status,created_at,updated_at,tags_json) VALUES(?,?,?,?,?,'PENDING',datetime('now'),datetime('now'),?)",
                    (root_task_id, plan_id, "GOAL", "Root", "xiaobo", json.dumps(["placeholder"], ensure_ascii=False)),
                )
                # create-plan can mark the plan-review CHECK node DONE and store its review; keep it.
                conn.execute(
                    "INSERT INTO task_nodes(task_id,plan_id,node_type,title,owner_agent_id,status,created_at,updated_at,tags_json) VALUES(?,?,?,?,?,'DONE',datetime('now'),datetime('now'),?)",
                    (plan_check_id, plan_id, "CHECK", "Plan Review", "xiaojing", json.dumps(["review", "plan"], ensure_ascii=False)),
                )
                # simulate run side-effects on an ACTION
                conn.execute(
                    """
                    INSERT INTO task_nodes(
                      task_id,plan_id,node_type,title,owner_agent_id,status,created_at,updated_at,active_artifact_id,approved_artifact_id
                    )
                    VALUES(?,?,?,?,?,'DONE',datetime('now'),datetime('now'),?,?)
                    """,
                    (action_id, plan_id, "ACTION", "Action", "xiaobo", "art_active", "art_approved"),
                )

                # artifacts + approvals (should be deleted)
                art_path = root / "artifact.txt"
                art_path.write_text("x", encoding="utf-8")
                conn.execute(
                    "INSERT INTO artifacts(artifact_id,task_id,name,path,format,version,sha256,created_at) VALUES(?,?,?,?,?,1,'s',datetime('now'))",
                    ("art_active", action_id, "a", str(art_path), "txt"),
                )
                conn.execute(
                    "INSERT INTO approvals(approval_id,artifact_id,status,approver,comment,decided_at,created_at) VALUES('ap1','art_active','APPROVED','u','c',datetime('now'),datetime('now'))"
                )

                # reviews: keep plan review, delete action review
                conn.execute(
                    "INSERT INTO reviews(review_id,task_id,reviewer_agent_id,total_score,breakdown_json,suggestions_json,summary,action_required,created_at) VALUES('r_plan',?,?,90,'[]','[]','ok','APPROVE',datetime('now'))",
                    (plan_check_id, "xiaojing"),
                )
                conn.execute(
                    "INSERT INTO reviews(review_id,task_id,reviewer_agent_id,total_score,breakdown_json,suggestions_json,summary,action_required,created_at) VALUES('r_act',?,?,10,'[]','[]','bad','MODIFY',datetime('now'))",
                    (action_id, "xiaojing"),
                )

                # skill run (deleted)
                conn.execute(
                    "INSERT INTO skill_runs(skill_run_id,task_id,plan_id,skill_name,inputs_json,params_json,status,output_artifacts_json,output_evidences_json,error_code,error_message,started_at,finished_at,idempotency_key) VALUES('s1',?,?, 'sk', '{}', NULL, 'DONE', NULL, NULL, NULL, NULL, datetime('now'), datetime('now'), 'k1')",
                    (action_id, plan_id),
                )

                # input requirement + evidence (evidence deleted)
                conn.execute(
                    "INSERT INTO input_requirements(requirement_id,task_id,name,kind,required,min_count,allowed_types_json,source,validation_json,created_at) VALUES('req1',?,?, 'file', 1, 1, '[]', 'x', '{}', datetime('now'))",
                    (action_id, "spec"),
                )
                conn.execute(
                    "INSERT INTO evidences(evidence_id,requirement_id,evidence_type,ref_id,ref_path,sha256,added_at) VALUES('e1','req1','file','f1',NULL,NULL,datetime('now'))"
                )

                # llm_calls rows (delete non PLAN_* for this plan)
                conn.execute(
                    "INSERT INTO llm_calls(llm_call_id,created_at,plan_id,task_id,agent,scope,provider,prompt_text,response_text,parsed_json,normalized_json,validator_error,error_code,error_message,runtime_context_hash,shared_prompt_version,shared_prompt_hash,agent_prompt_version,agent_prompt_hash,started_at_ts,finished_at_ts,meta_json) VALUES('c1',datetime('now'),?,NULL,'xiaobo','TASK_ACTION','x',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'h','1','h','1','h',0,1,'{}')",
                    (plan_id,),
                )

                # task_events: delete task_id not null
                conn.execute(
                    "INSERT INTO task_events(event_id,plan_id,task_id,event_type,payload_json,created_at) VALUES('ev1',?,?, 'STATUS_CHANGED','{}',datetime('now'))",
                    (plan_id, action_id),
                )
                conn.execute(
                    "INSERT INTO task_events(event_id,plan_id,task_id,event_type,payload_json,created_at) VALUES('evp',?,NULL, 'PLAN_APPROVED','{}',datetime('now'))",
                    (plan_id,),
                )

                # audit event linked to plan (deleted)
                conn.execute(
                    "INSERT INTO audit_events(audit_id,created_at,category,action,top_task_hash,top_task_title,plan_id,task_id,llm_call_id,job_id,status_before,status_after,ok,message,payload_json) VALUES('a1',datetime('now'),'API_CALL','RUN_START',NULL,NULL,?,NULL,NULL,NULL,NULL,NULL,1,'x','{}')",
                    (plan_id,),
                )

                conn.commit()

                # filesystem side effects
                (ws / "artifacts" / action_id).mkdir(parents=True)
                ((ws / "artifacts" / action_id) / "a.txt").write_text("x", encoding="utf-8")
                (ws / "reviews" / action_id).mkdir(parents=True)
                ((ws / "reviews" / action_id) / "review.json").write_text("x", encoding="utf-8")
                (ws / "reviews" / plan_check_id).mkdir(parents=True)
                ((ws / "reviews" / plan_check_id) / "review.json").write_text("keep", encoding="utf-8")
                (ws / "required_docs").mkdir(parents=True)
                (ws / "required_docs" / f"{action_id}.md").write_text("x", encoding="utf-8")
                (ws / "deliverables" / plan_id).mkdir(parents=True)
                ((ws / "deliverables" / plan_id) / "final.json").write_text("x", encoding="utf-8")

                res = reset_plan_to_pre_run(conn, plan_id=plan_id, workspace_dir=ws)
                self.assertEqual(res.plan_id, plan_id)

                # statuses restored
                st_root = conn.execute("SELECT status FROM task_nodes WHERE task_id=?", (root_task_id,)).fetchone()["status"]
                st_check = conn.execute("SELECT status FROM task_nodes WHERE task_id=?", (plan_check_id,)).fetchone()["status"]
                st_act = conn.execute("SELECT status FROM task_nodes WHERE task_id=?", (action_id,)).fetchone()["status"]
                self.assertEqual(st_root, "PENDING")
                self.assertEqual(st_check, "DONE")
                self.assertEqual(st_act, "PENDING")

                # DB side-effects removed
                self.assertEqual(conn.execute("SELECT COUNT(1) FROM artifacts").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(1) FROM approvals").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(1) FROM skill_runs").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(1) FROM evidences").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(1) FROM llm_calls WHERE plan_id=?", (plan_id,)).fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(1) FROM task_events WHERE plan_id=? AND task_id IS NOT NULL", (plan_id,)).fetchone()[0], 0)

                # keep plan-level event
                self.assertEqual(conn.execute("SELECT COUNT(1) FROM task_events WHERE plan_id=? AND task_id IS NULL", (plan_id,)).fetchone()[0], 1)

                # keep only plan review review row
                self.assertEqual(conn.execute("SELECT COUNT(1) FROM reviews WHERE task_id=?", (plan_check_id,)).fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(1) FROM reviews WHERE task_id=?", (action_id,)).fetchone()[0], 0)

                # filesystem side-effects removed, plan review kept
                self.assertFalse((ws / "artifacts" / action_id).exists())
                self.assertFalse((ws / "reviews" / action_id).exists())
                self.assertTrue((ws / "reviews" / plan_check_id).exists())
                self.assertFalse((ws / "required_docs" / f"{action_id}.md").exists())
                self.assertFalse((ws / "deliverables" / plan_id).exists())
            finally:
                conn.close()


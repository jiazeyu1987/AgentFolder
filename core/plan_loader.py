from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict

from core.models import as_list_of_str, parse_plan_meta, validate_plan_dict
from core.util import canonical_json, utc_now_iso


def load_plan_json(path: Path) -> Dict[str, Any]:
    plan_dict = json.loads(path.read_text(encoding="utf-8"))
    validate_plan_dict(plan_dict)
    return plan_dict


def upsert_plan(conn: sqlite3.Connection, plan_dict: Dict[str, Any]) -> str:
    meta = parse_plan_meta(plan_dict)
    conn.execute(
        """
        INSERT INTO plans(plan_id, title, owner_agent_id, root_task_id, created_at, constraints_json)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(plan_id) DO UPDATE SET
          title=excluded.title,
          owner_agent_id=excluded.owner_agent_id,
          root_task_id=excluded.root_task_id,
          constraints_json=excluded.constraints_json
        """,
        (
            meta.plan_id,
            meta.title,
            meta.owner_agent_id,
            meta.root_task_id,
            meta.created_at,
            canonical_json(meta.constraints),
        ),
    )

    now = utc_now_iso()
    for node in plan_dict["nodes"]:
        tags = as_list_of_str(node.get("tags"))
        # v2 columns are optional; best-effort insert if they exist.
        epd = node.get("estimated_person_days")
        deliverable = node.get("deliverable_spec_json") or node.get("deliverable_spec")
        acceptance = node.get("acceptance_criteria_json") or node.get("acceptance_criteria")
        review_target = node.get("review_target_task_id")
        review_output = node.get("review_output_spec_json") or node.get("review_output_spec")

        def _norm_json(v: Any) -> str | None:
            if v is None:
                return None
            if isinstance(v, str):
                s = v.strip()
                return s if s else None
            if isinstance(v, (dict, list)):
                return canonical_json(v)
            return canonical_json(str(v))

        try:
            conn.execute(
                """
                INSERT INTO task_nodes(
                  task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, tags_json,
                  priority, status, blocked_reason, attempt_count, confidence, active_branch,
                  active_artifact_id, created_at, updated_at,
                  estimated_person_days, deliverable_spec_json, acceptance_criteria_json,
                  review_target_task_id, review_output_spec_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, 0, 0.5, 1, NULL, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  plan_id=excluded.plan_id,
                  node_type=excluded.node_type,
                  title=excluded.title,
                  goal_statement=excluded.goal_statement,
                  rationale=excluded.rationale,
                  owner_agent_id=excluded.owner_agent_id,
                  tags_json=excluded.tags_json,
                  priority=excluded.priority,
                  estimated_person_days=COALESCE(excluded.estimated_person_days, task_nodes.estimated_person_days),
                  deliverable_spec_json=COALESCE(excluded.deliverable_spec_json, task_nodes.deliverable_spec_json),
                  acceptance_criteria_json=COALESCE(excluded.acceptance_criteria_json, task_nodes.acceptance_criteria_json),
                  review_target_task_id=COALESCE(excluded.review_target_task_id, task_nodes.review_target_task_id),
                  review_output_spec_json=COALESCE(excluded.review_output_spec_json, task_nodes.review_output_spec_json),
                  updated_at=excluded.updated_at
                """,
                (
                    node["task_id"],
                    node["plan_id"],
                    node["node_type"],
                    node["title"],
                    node.get("goal_statement"),
                    node.get("rationale"),
                    node["owner_agent_id"],
                    canonical_json(tags),
                    int(node.get("priority") or 0),
                    now,
                    now,
                    epd,
                    _norm_json(deliverable),
                    _norm_json(acceptance),
                    str(review_target) if isinstance(review_target, str) and review_target.strip() else None,
                    _norm_json(review_output),
                ),
            )
        except sqlite3.OperationalError:
            conn.execute(
                """
                INSERT INTO task_nodes(
                  task_id, plan_id, node_type, title, goal_statement, rationale, owner_agent_id, tags_json,
                  priority, status, blocked_reason, attempt_count, confidence, active_branch,
                  active_artifact_id, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, 0, 0.5, 1, NULL, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  plan_id=excluded.plan_id,
                  node_type=excluded.node_type,
                  title=excluded.title,
                  goal_statement=excluded.goal_statement,
                  rationale=excluded.rationale,
                  owner_agent_id=excluded.owner_agent_id,
                  tags_json=excluded.tags_json,
                  priority=excluded.priority,
                  updated_at=excluded.updated_at
                """,
                (
                    node["task_id"],
                    node["plan_id"],
                    node["node_type"],
                    node["title"],
                    node.get("goal_statement"),
                    node.get("rationale"),
                    node["owner_agent_id"],
                    canonical_json(tags),
                    int(node.get("priority") or 0),
                    now,
                    now,
                ),
            )

    for edge in plan_dict["edges"]:
        conn.execute(
            """
            INSERT INTO task_edges(edge_id, plan_id, from_task_id, to_task_id, edge_type, metadata_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(edge_id) DO UPDATE SET
              plan_id=excluded.plan_id,
              from_task_id=excluded.from_task_id,
              to_task_id=excluded.to_task_id,
              edge_type=excluded.edge_type,
              metadata_json=excluded.metadata_json
            """,
            (
                edge["edge_id"],
                edge["plan_id"],
                edge["from_task_id"],
                edge["to_task_id"],
                edge["edge_type"],
                canonical_json(edge.get("metadata") or {}),
                now,
            ),
        )

    for req in plan_dict["requirements"]:
        allowed_types = as_list_of_str(req.get("allowed_types"))
        validation = req.get("validation") if isinstance(req.get("validation"), dict) else None
        conn.execute(
            """
            INSERT INTO input_requirements(
              requirement_id, task_id, name, kind, required, min_count, allowed_types_json, source, validation_json, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(requirement_id) DO UPDATE SET
              task_id=excluded.task_id,
              name=excluded.name,
              kind=excluded.kind,
              required=excluded.required,
              min_count=excluded.min_count,
              allowed_types_json=excluded.allowed_types_json,
              source=excluded.source,
              validation_json=excluded.validation_json
            """,
            (
                req["requirement_id"],
                req["task_id"],
                req["name"],
                req["kind"],
                int(req["required"]),
                int(req.get("min_count") or 1),
                canonical_json([t.lower() for t in allowed_types]),
                req["source"],
                canonical_json(validation or {}),
                now,
            ),
        )

    return meta.plan_id


def plan_exists(conn: sqlite3.Connection, plan_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    return row is not None


def load_plan_into_db_if_needed(conn: sqlite3.Connection, plan_path: Path) -> str:
    plan_dict = load_plan_json(plan_path)
    plan_id = plan_dict["plan"]["plan_id"]
    upsert_plan(conn, plan_dict)
    conn.commit()
    return plan_id

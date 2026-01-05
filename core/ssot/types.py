from __future__ import annotations

from typing import Any, Dict, List


SNAPSHOT_SCHEMA_VERSION = "plan_snapshot_v1"

REASON_CODES: List[str] = [
    "WAITING_REVIEW",
    "WAITING_INPUT",
    "WAITING_EXTERNAL",
    "BLOCKED",
    "FAILED",
    "RUNNABLE",
    "DONE",
]

SNAPSHOT_SCHEMA_SUMMARY: Dict[str, Any] = {
    "schema_version": SNAPSHOT_SCHEMA_VERSION,
    "required_top_level_keys": [
        "schema_version",
        "ts",
        "plan",
        "job",
        "summary",
        "reasons",
        "inputs_needed",
        "waiting_review",
        "recent_errors",
        "final_deliverable",
        "doctor",
        "feasibility",
        "report",
        "manifest",
    ],
    "reason_codes": REASON_CODES,
    "notes": "SSOT snapshot for CLI/backend/UI; adapters must not re-infer reasons.",
}


DELIVERABLES_MANIFEST_SCHEMA_VERSION = "deliverables_manifest_v1"
DELIVERABLES_FINAL_SCHEMA_VERSION = "deliverables_final_v1"

MANIFEST_SCHEMA_SUMMARY: Dict[str, Any] = {
    "schema_version": DELIVERABLES_MANIFEST_SCHEMA_VERSION,
    "required_top_level_keys": ["schema_version", "plan", "files", "bundle_mode", "entrypoint"],
    "bundle_mode_enum": ["SINGLE", "MANIFEST"],
    "file_required_keys": ["task_id", "task_title", "node_type", "status", "owner_agent_id", "artifact"],
    "artifact_required_keys": ["artifact_id", "format", "sha256", "source_path", "dest_path"],
    "notes": "deliverables manifest used by export/cleanup/UI; default export lists DONE ACTION approved artifacts.",
}

FINAL_SCHEMA_SUMMARY: Dict[str, Any] = {
    "schema_version": DELIVERABLES_FINAL_SCHEMA_VERSION,
    "required_top_level_keys": ["schema_version", "final_entrypoint", "final_task_title", "final_artifact_id", "how_to_run"],
    "notes": "deliverables final.json is the single entrypoint for users; points to one file under deliverables dir.",
}


def validate_snapshot_schema(obj: Dict[str, Any]) -> List[str]:
    """
    Minimal guard to help tests catch accidental drift.
    Returns list of missing required top-level keys.
    """
    missing: List[str] = []
    for k in SNAPSHOT_SCHEMA_SUMMARY["required_top_level_keys"]:
        if k not in obj:
            missing.append(str(k))
    return missing

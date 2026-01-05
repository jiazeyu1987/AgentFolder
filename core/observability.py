from __future__ import annotations

"""
Backward-compatible wrapper for the SSOT snapshot implementation.

Phase A rule: snapshot schema + explanation logic must live in one place only.
This module re-exports the SSOT entrypoints so existing imports keep working.
"""

from typing import Any, Dict, List

from core.ssot.snapshot import get_plan_snapshot, render_snapshot_brief, render_snapshot_md
from core.ssot.types import REASON_CODES, SNAPSHOT_SCHEMA_SUMMARY, SNAPSHOT_SCHEMA_VERSION, validate_snapshot_schema

ReasonCode = str

__all__ = [
    "ReasonCode",
    "SNAPSHOT_SCHEMA_VERSION",
    "REASON_CODES",
    "SNAPSHOT_SCHEMA_SUMMARY",
    "validate_snapshot_schema",
    "get_plan_snapshot",
    "render_snapshot_brief",
    "render_snapshot_md",
]


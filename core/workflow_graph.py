"""
Backward-compatible facade for the LLM workflow read model.

PR2: the SQL implementation lives in `core.queries.workflow_query`.
"""

from core.queries.workflow_query import WorkflowQuery, build_workflow

__all__ = ["WorkflowQuery", "build_workflow"]


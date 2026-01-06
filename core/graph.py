"""
Backward-compatible facade for the Graph read model.

PR2: the SQL implementation lives in `core.queries.graph_query`.
"""

from core.queries.graph_query import GraphQueryResult, build_plan_graph, _parse_required_docs_md

__all__ = ["GraphQueryResult", "build_plan_graph", "_parse_required_docs_md"]


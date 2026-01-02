import dagre from "dagre";
import { Position, type Edge, type Node } from "reactflow";

export function layoutDagre(nodes: Node[], edges: Edge[], direction: "TB" | "LR" = "TB"): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, ranksep: 70, nodesep: 35, marginx: 20, marginy: 20 });

  for (const n of nodes) {
    const w = (n.width as number | undefined) ?? 220;
    const h = (n.height as number | undefined) ?? 60;
    g.setNode(n.id, { width: w, height: h });
  }
  for (const e of edges) {
    g.setEdge(e.source, e.target);
  }

  dagre.layout(g);

  const outNodes = nodes.map((n) => {
    const p = g.node(n.id);
    return {
      ...n,
      position: { x: p.x - p.width / 2, y: p.y - p.height / 2 },
      sourcePosition: direction === "LR" ? Position.Right : Position.Bottom,
      targetPosition: direction === "LR" ? Position.Left : Position.Top,
    };
  });
  return { nodes: outNodes, edges };
}

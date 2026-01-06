import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  Edge,
  Handle,
  MarkerType,
  MiniMap,
  Node,
  Position,
  ReactFlowInstance,
} from "reactflow";
import type { GraphEdge, GraphNode } from "../types";
import { layoutDagre } from "../graphLayout";

function statusColor(status: string): string {
  switch (status) {
    case "READY":
      return "#18a34a";
    case "IN_PROGRESS":
      return "#f59e0b";
    case "BLOCKED":
      return "#f97316";
    case "READY_TO_CHECK":
      return "#3b82f6";
    case "TO_BE_MODIFY":
      return "#a855f7";
    case "DONE":
      return "#64748b";
    case "FAILED":
      return "#ef4444";
    default:
      return "#94a3b8";
  }
}

function edgeStyle(t: string) {
  if (t === "DECOMPOSE") return { stroke: "#94a3b8", strokeDasharray: "6 4", strokeWidth: 2 };
  if (t === "ALTERNATIVE") return { stroke: "#38bdf8", strokeDasharray: "2 6", strokeWidth: 2 };
  return { stroke: "#e2e8f0", strokeWidth: 2 };
}

function edgeLabel(t: string) {
  if (t === "DEPENDS_ON") return "DEPENDS_ON";
  if (t === "DECOMPOSE") return "DECOMPOSE";
  if (t === "ALTERNATIVE") return "ALT";
  return t;
}

function edgeColor(t: string) {
  if (t === "DECOMPOSE") return "#94a3b8";
  if (t === "ALTERNATIVE") return "#38bdf8";
  return "#e2e8f0";
}

function TaskNode(props: { id: string; data: { label: string; status: string; color: string; isRunning: boolean } }) {
  const { data } = props;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 200 }}>
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      <div style={{ fontWeight: 700, fontSize: 12, lineHeight: "14px" }}>{data.label}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
        <span className="pill" style={{ background: data.color }}>
          {data.status}
        </span>
        <span className="mono">{props.id.slice(0, 8)}</span>
        {data.isRunning ? <span className="mono" style={{ color: "#fbbf24" }}>RUN</span> : null}
      </div>
    </div>
  );
}

const NODE_TYPES = { task: TaskNode } as const;

export default function TaskGraph(props: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onSelectNode: (taskId: string) => void;
}) {
  const nodeById = useMemo(() => new Map(props.nodes.map((n) => [n.task_id, n])), [props.nodes]);

  const topologyKey = useMemo(() => {
    const nodeIds = props.nodes.map((n) => n.task_id).sort().join(",");
    const edges = props.edges
      .map((e) => `${e.from_task_id}>${e.to_task_id}:${e.edge_type}`)
      .sort()
      .join("|");
    return `${nodeIds}::${edges}`;
  }, [props.nodes, props.edges]);

  const mkRfNodes = (positions?: Map<string, { x: number; y: number }>): Node[] => {
    return props.nodes.map((n) => ({
      id: n.task_id,
      type: "task",
      data: { label: n.title, status: n.status, color: statusColor(n.status), isRunning: n.is_running },
      position: positions?.get(n.task_id) ?? { x: 0, y: 0 },
      style: {
        border: n.is_running ? "2px solid #fbbf24" : "1px solid #334155",
        borderRadius: 10,
        padding: 10,
        background: "#0b1220",
        color: "#e2e8f0",
        boxShadow: n.is_running ? "0 0 0 4px rgba(251,191,36,0.15)" : undefined,
      },
      className: "taskNode",
    }));
  };

  const mkRfEdges = (): Edge[] => {
    return props.edges.map((e) => ({
      id: e.edge_id,
      source: e.from_task_id,
      target: e.to_task_id,
      type: "smoothstep",
      style: edgeStyle(e.edge_type),
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: edgeColor(e.edge_type),
        width: 18,
        height: 18,
      },
      label: edgeLabel(e.edge_type),
      labelStyle: { fill: "#e2e8f0", fontSize: 10 },
      labelBgStyle: { fill: "#0b1220", fillOpacity: 0.9 },
      labelBgPadding: [4, 2],
      labelBgBorderRadius: 6,
    }));
  };

  const [laidNodes, setLaidNodes] = useState<Node[]>(() => []);
  const [laidEdges, setLaidEdges] = useState<Edge[]>(() => []);
  const rfRef = useRef<ReactFlowInstance | null>(null);
  const [needsFitView, setNeedsFitView] = useState(false);

  useEffect(() => {
    // Topology changed (new nodes/edges): compute a fresh layout and fit view once.
    const rfNodes = mkRfNodes();
    const rfEdges = mkRfEdges();
    const laid = layoutDagre(rfNodes, rfEdges, "TB");
    setLaidNodes(laid.nodes);
    setLaidEdges(laid.edges);
    setNeedsFitView(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topologyKey]);

  useEffect(() => {
    // Status-only update: keep existing positions and avoid re-layout.
    if (!laidNodes.length) return;
    const pos = new Map<string, { x: number; y: number }>();
    for (const n of laidNodes) pos.set(n.id, { x: n.position.x, y: n.position.y });
    setLaidNodes(mkRfNodes(pos));
    // edges rarely need style changes without topology changes, but keep in sync for safety
    setLaidEdges(mkRfEdges());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.nodes]);

  useEffect(() => {
    if (!needsFitView) return;
    const inst = rfRef.current;
    if (!inst) return;
    try {
      inst.fitView({ padding: 0.2 });
    } catch {
      // ignore
    }
    setNeedsFitView(false);
  }, [needsFitView]);

  return (
    <div className="graph">
      <ReactFlow
        nodes={laidNodes}
        edges={laidEdges}
        onInit={(inst) => {
          rfRef.current = inst;
          // Fit view after first init when layout is ready.
          setNeedsFitView(true);
        }}
        onNodeClick={(_, node) => props.onSelectNode(node.id)}
        nodeTypes={NODE_TYPES}
        zoomOnScroll
        preventScrolling
      >
        <MiniMap nodeColor={(n) => (nodeById.get(n.id) ? statusColor(nodeById.get(n.id)!.status) : "#94a3b8")} maskColor="rgba(2,6,23,0.7)" />
        <Controls />
        <Background gap={24} size={1} color="#1f2937" />
      </ReactFlow>
    </div>
  );
}

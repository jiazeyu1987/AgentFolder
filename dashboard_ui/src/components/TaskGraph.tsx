import React, { useMemo } from "react";
import ReactFlow, { Background, Controls, Edge, MarkerType, MiniMap, Node } from "reactflow";
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
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
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

export default function TaskGraph(props: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onSelectNode: (taskId: string) => void;
}) {
  const nodeById = useMemo(() => new Map(props.nodes.map((n) => [n.task_id, n])), [props.nodes]);

  const rfNodes: Node[] = useMemo(() => {
    return props.nodes.map((n) => ({
      id: n.task_id,
      type: "task",
      data: { label: n.title, status: n.status, color: statusColor(n.status), isRunning: n.is_running },
      position: { x: 0, y: 0 },
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
  }, [props.nodes]);

  const rfEdges: Edge[] = useMemo(() => {
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
  }, [props.edges]);

  const { nodes: laidNodes, edges: laidEdges } = useMemo(() => layoutDagre(rfNodes, rfEdges, "TB"), [rfNodes, rfEdges]);

  return (
    <div className="graph">
      <ReactFlow
        nodes={laidNodes}
        edges={laidEdges}
        fitView
        onNodeClick={(_, node) => props.onSelectNode(node.id)}
        nodeTypes={{ task: TaskNode }}
      >
        <MiniMap nodeColor={(n) => (nodeById.get(n.id) ? statusColor(nodeById.get(n.id)!.status) : "#94a3b8")} maskColor="rgba(2,6,23,0.7)" />
        <Controls />
        <Background gap={24} size={1} color="#1f2937" />
      </ReactFlow>
    </div>
  );
}

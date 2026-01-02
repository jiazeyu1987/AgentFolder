import React, { useMemo } from "react";
import ReactFlow, { Background, Controls, Edge, Handle, MarkerType, MiniMap, Node, Position } from "reactflow";
import type { WorkflowResp } from "../types";
import { layoutDagre } from "../graphLayout";

function nodeColor(n: WorkflowResp["nodes"][number]): string {
  if (n.error_code || n.validator_error) return "#ef4444";
  if (n.scope === "PLAN_REVIEW") return "#38bdf8";
  if (n.scope === "PLAN_GEN") return "#a855f7";
  return "#94a3b8";
}

function edgeStyle(t: string) {
  if (t === "PAIR") return { stroke: "#38bdf8", strokeWidth: 2 };
  return { stroke: "#e2e8f0", strokeWidth: 2 };
}

function edgeColor(t: string) {
  if (t === "PAIR") return "#38bdf8";
  return "#e2e8f0";
}

function LlmNode(props: { id: string; data: { n: WorkflowResp["nodes"][number] } }) {
  const n = props.data.n;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 220 }}>
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      <div style={{ fontWeight: 700, fontSize: 12, lineHeight: "14px" }}>{n.scope}</div>
      <div className="muted" style={{ fontSize: 12 }}>
        <span className="mono">{n.created_at}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
        <span className="pill" style={{ background: nodeColor(n) }}>
          {n.agent}
        </span>
        <span className="mono">a={n.attempt}</span>
        {n.scope === "PLAN_REVIEW" ? <span className="mono">r={n.review_attempt}</span> : null}
        {n.error_code || n.validator_error ? <span className="mono">ERR</span> : <span className="mono">OK</span>}
      </div>
    </div>
  );
}

export default function LLMWorkflowGraph(props: {
  workflow: WorkflowResp;
  onSelectCall: (llmCallId: string) => void;
}) {
  const nodes: Node[] = useMemo(() => {
    return props.workflow.nodes.map((n) => ({
      id: n.llm_call_id,
      type: "llm",
      data: { n },
      position: { x: 0, y: 0 },
      style: {
        border: n.error_code || n.validator_error ? "2px solid #ef4444" : "1px solid #334155",
        borderRadius: 10,
        padding: 10,
        background: "#0b1220",
        color: "#e2e8f0",
      },
    }));
  }, [props.workflow.nodes]);

  const edges: Edge[] = useMemo(() => {
    return props.workflow.edges.map((e, idx) => ({
      id: `we_${idx}_${e.from}_${e.to}`,
      source: e.from,
      target: e.to,
      type: "smoothstep",
      style: edgeStyle(e.edge_type),
      markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor(e.edge_type), width: 18, height: 18 },
    }));
  }, [props.workflow.edges]);

  const { nodes: laidNodes, edges: laidEdges } = useMemo(() => layoutDagre(nodes, edges, "TB"), [nodes, edges]);

  return (
    <div className="graph">
      <ReactFlow
        nodes={laidNodes}
        edges={laidEdges}
        fitView
        onNodeClick={(_, node) => props.onSelectCall(node.id)}
        nodeTypes={{ llm: LlmNode }}
      >
        <MiniMap nodeColor={(n) => (props.workflow.nodes.find((x) => x.llm_call_id === n.id) ? nodeColor(props.workflow.nodes.find((x) => x.llm_call_id === n.id)!) : "#94a3b8")} maskColor="rgba(2,6,23,0.7)" />
        <Controls />
        <Background gap={24} size={1} color="#1f2937" />
      </ReactFlow>
    </div>
  );
}


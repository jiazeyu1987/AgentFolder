import React, { useMemo } from "react";
import ReactFlow, { Background, Controls, Edge, Handle, MarkerType, MiniMap, Node, Position } from "reactflow";
import type { WorkflowResp } from "../types";
import { formatLocalDateTime, formatLocalTime } from "../time";

function nodeColor(n: WorkflowResp["nodes"][number]): string {
  if (n.error_code || n.validator_error) return "#ef4444";
  const st = String(n.stage || "").toUpperCase();
  if (st === "STRUCTURE") return "#a855f7";
  if (st === "BINDINGS") return "#22c55e";
  if (st === "EXECUTION") return "#f59e0b";
  if (n.scope === "PLAN_REVIEW") return "#38bdf8";
  if (n.scope === "PLAN_GEN") return "#a855f7";
  return "#94a3b8";
}

function edgeStyle(t: string) {
  if (t === "PAIR") return { stroke: "#38bdf8", strokeWidth: 2 };
  if (t === "STAGE_NEXT") return { stroke: "#e2e8f0", strokeWidth: 2 };
  return { stroke: "rgba(226,232,240,0.25)", strokeWidth: 1 };
}

function edgeColor(t: string) {
  if (t === "PAIR") return "#38bdf8";
  if (t === "STAGE_NEXT") return "#e2e8f0";
  return "#e2e8f0";
}

function LlmNode(props: { id: string; data: { n: WorkflowResp["nodes"][number] } }) {
  const n = props.data.n;
  const err = n.error_code ? `err=${n.error_code}` : n.validator_error ? "validator_error" : "";
  const score = typeof n.total_score === "number" ? n.total_score : null;
  const stage = String(n.stage || "").toUpperCase();
  const isGenClone = String(n.node_kind || "") === "GEN_CLONE";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 220 }}>
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      <div style={{ fontWeight: 700, fontSize: 12, lineHeight: "14px" }}>
        {(isGenClone ? "GEN" : n.scope)} {stage ? <span className="muted">[{stage}]</span> : null}
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        <span className="mono" title={formatLocalDateTime(n.created_at)}>
          {formatLocalTime(n.created_at)}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
        <span className="pill" style={{ background: nodeColor(n) }}>
          {n.agent}
        </span>
        <span className="mono">a={n.attempt}</span>
        {!isGenClone && n.scope === "PLAN_REVIEW" ? <span className="mono">r={n.review_attempt}</span> : null}
        {!isGenClone && n.scope === "PLAN_REVIEW" && typeof score === "number" ? <span className="mono">score={score}</span> : null}
        {n.error_code || n.validator_error ? <span className="mono">{err || "ERR"}</span> : <span className="mono">OK</span>}
      </div>
    </div>
  );
}

const NODE_TYPES = { llm: LlmNode } as const;

export default function LLMWorkflowGraph(props: {
  workflow: WorkflowResp;
  onSelectCall: (llmCallId: string) => void;
}) {
  const stageOrder = ["STRUCTURE", "BINDINGS", "EXECUTION", "UNKNOWN"] as const;
  const stageIndex = (st: string) => {
    const s = String(st || "").toUpperCase() || "UNKNOWN";
    const i = stageOrder.indexOf(s as any);
    return i >= 0 ? i : stageOrder.length - 1;
  };

  const nodes: Node[] = useMemo(() => {
    // Stage-lane layout: x by stage, y by time within stage.
    const laneX = 420;
    const rowY = 120;
    const byStage: Record<string, Array<WorkflowResp["nodes"][number]>> = {};
    for (const n of props.workflow.nodes) {
      const st = String(n.stage || "").toUpperCase() || "UNKNOWN";
      byStage[st] = byStage[st] || [];
      byStage[st].push(n);
    }
    for (const st of Object.keys(byStage)) {
      byStage[st].sort((a, b) => {
        const c = String(a.created_at).localeCompare(String(b.created_at));
        if (c !== 0) return c;
        return String(a.llm_call_id).localeCompare(String(b.llm_call_id));
      });
    }

    const pos = new Map<string, { x: number; y: number }>();
    for (const st of Object.keys(byStage)) {
      const idx = stageIndex(st);
      const arr = byStage[st];
      for (let i = 0; i < arr.length; i++) {
        pos.set(arr[i].llm_call_id, { x: idx * laneX, y: i * rowY });
      }
    }

    return props.workflow.nodes.map((n) => ({
      id: n.llm_call_id,
      type: "llm",
      data: { n },
      position: pos.get(n.llm_call_id) ?? { x: 0, y: 0 },
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
    // Prefer stage chains; keep PAIR only as secondary. Hide global NEXT to reduce noise.
    const visible = props.workflow.edges.filter((e) => e.edge_type === "STAGE_NEXT" || e.edge_type === "PAIR");
    return visible.map((e, idx) => ({
      id: `we_${idx}_${e.from}_${e.to}`,
      source: e.from,
      target: e.to,
      type: "smoothstep",
      style: edgeStyle(e.edge_type),
      markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor(e.edge_type), width: 18, height: 18 },
    }));
  }, [props.workflow.edges]);

  return (
    <div className="graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        onNodeClick={(_, node) => {
          props.onSelectCall(node.id);
        }}
        nodeTypes={NODE_TYPES}
        preventScrolling={false}
      >
        <MiniMap nodeColor={(n) => (props.workflow.nodes.find((x) => x.llm_call_id === n.id) ? nodeColor(props.workflow.nodes.find((x) => x.llm_call_id === n.id)!) : "#94a3b8")} maskColor="rgba(2,6,23,0.7)" />
        <Controls />
        <Background gap={24} size={1} color="#1f2937" />
      </ReactFlow>
    </div>
  );
}

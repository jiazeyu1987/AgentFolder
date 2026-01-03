import React, { useEffect, useMemo, useState } from "react";
import type { GraphNode, TaskDetailsResp, TaskLlmCallsResp } from "../types";
import * as api from "../api";

function groupByAgent(calls: TaskLlmCallsResp["calls"]) {
  const m = new Map<string, TaskLlmCallsResp["calls"]>();
  for (const c of calls) {
    const key = c.agent || "unknown";
    const arr = m.get(key) ?? [];
    arr.push(c);
    m.set(key, arr);
  }
  return Array.from(m.entries()).map(([agent, items]) => ({ agent, items }));
}

export default function NodeDetails(props: { node: GraphNode | null }) {
  const n = props.node;
  const [details, setDetails] = useState<TaskDetailsResp | null>(null);
  const [detailsErr, setDetailsErr] = useState<string>("");
  const [llm, setLlm] = useState<TaskLlmCallsResp | null>(null);
  const [llmErr, setLlmErr] = useState<string>("");

  useEffect(() => {
    setDetails(null);
    setDetailsErr("");
    setLlm(null);
    setLlmErr("");
    if (!n) return;
    api.getTaskDetails(n.task_id).then(setDetails).catch((e) => setDetailsErr(String(e)));
    api.getTaskLlmCalls(n.task_id, 50).then(setLlm).catch((e) => setLlmErr(String(e)));
  }, [n?.task_id]);

  const groups = useMemo(() => (llm ? groupByAgent(llm.calls) : []), [llm]);

  if (!n) {
    return (
      <div className="panel">
        <h3>Node</h3>
        <div className="muted">click a node to see details</div>
      </div>
    );
  }

  return (
    <div className="panel">
      <h3>Node</h3>
      <div className="kv">
        <div className="k">title</div>
        <div className="v">{n.title}</div>
        <div className="k">node_type</div>
        <div className="v">{n.node_type}</div>
        <div className="k">status</div>
        <div className="v">{n.status}</div>
        <div className="k">owner</div>
        <div className="v" style={{ fontWeight: 900, color: "#38bdf8" }}>{n.owner_agent_id}</div>
        <div className="k">blocked_reason</div>
        <div className="v">{n.blocked_reason ?? "-"}</div>
        <div className="k">attempts</div>
        <div className="v">{n.attempt_count}</div>
      </div>

      <h4 style={{ color: "#fbbf24" }}>Deliverables</h4>
      {detailsErr ? <div className="muted">load failed: {detailsErr}</div> : null}
      {!details ? <div className="muted">loading...</div> : null}
      {details ? (
        details.active_artifact ? (
          <div className="muted">
            <span className="mono">{details.active_artifact.path}</span> ({details.active_artifact.format})
          </div>
        ) : (
          <div className="muted">none</div>
        )
      ) : null}

      {n.missing_inputs && n.missing_inputs.length ? (
        <>
          <h4>Missing Inputs</h4>
          <ul className="list">
            {n.missing_inputs.slice(0, 20).map((m, idx) => (
              <li key={idx}>
                <div className="mono">{m.name}</div>
                {m.suggested_path ? <div className="mono">{m.suggested_path}</div> : null}
                {m.accepted_types ? (
                  <div className="muted">types: {Array.isArray(m.accepted_types) ? m.accepted_types.join(",") : String(m.accepted_types)}</div>
                ) : null}
              </li>
            ))}
          </ul>
          <div className="muted">
            required_docs: <span className="mono">{n.required_docs_path}</span>
          </div>
        </>
      ) : null}

      <h4>Artifacts / Reviews</h4>
      <div className="kv">
        <div className="k">artifact_dir</div>
        <div className="v mono">{n.artifact_dir}</div>
        <div className="k">review_dir</div>
        <div className="v mono">{n.review_dir}</div>
      </div>

      <h4>Agent Prompts / Outputs</h4>
      {llmErr ? <div className="muted">load failed: {llmErr}</div> : null}
      {!llm ? <div className="muted">loading...</div> : null}
      {llm && llm.calls.length === 0 ? <div className="muted">no llm_calls for this task yet</div> : null}
      {groups.map((g) => (
        <details key={g.agent} open>
          <summary className="mono">
            {g.agent} ({g.items.length})
          </summary>
          {g.items.slice(0, 30).map((c) => (
            <details key={c.llm_call_id} className="call">
              <summary className="muted">
                {c.created_at} scope={c.scope} {c.error_code ? ` error=${c.error_code}` : ""}
              </summary>
          <div className="callGrid">
                <div className="callLabel">Prompt</div>
                <pre className="callText">{c.prompt_text ?? ""}</pre>
                <div className="callLabel">Raw Response</div>
                <pre className="callText">{c.response_text ?? ""}</pre>
              </div>
              {c.validator_error ? <div className="muted">validator_error: {c.validator_error}</div> : null}
            </details>
          ))}
        </details>
      ))}
    </div>
  );
}

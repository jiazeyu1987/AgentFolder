import React from "react";
import type { GraphNode } from "../types";

export default function NodeDetails(props: { node: GraphNode | null }) {
  const n = props.node;
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
        <div className="k">task_id</div>
        <div className="v mono">{n.task_id}</div>
        <div className="k">node_type</div>
        <div className="v">{n.node_type}</div>
        <div className="k">status</div>
        <div className="v">{n.status}</div>
        <div className="k">owner</div>
        <div className="v">{n.owner_agent_id}</div>
        <div className="k">blocked_reason</div>
        <div className="v">{n.blocked_reason ?? "-"}</div>
        <div className="k">attempts</div>
        <div className="v">{n.attempt_count}</div>
      </div>

      <h4>Missing Inputs</h4>
      {n.missing_inputs && n.missing_inputs.length ? (
        <ul className="list">
          {n.missing_inputs.slice(0, 20).map((m, idx) => (
            <li key={idx}>
              <div className="mono">{m.name}</div>
              {m.suggested_path ? <div>→ {m.suggested_path}</div> : null}
              {m.accepted_types ? <div className="muted">types: {Array.isArray(m.accepted_types) ? m.accepted_types.join(",") : String(m.accepted_types)}</div> : null}
            </li>
          ))}
        </ul>
      ) : (
        <div className="muted">none</div>
      )}
      <div className="muted">
        required_docs: <span className="mono">{n.required_docs_path}</span>
      </div>

      <h4>Artifacts / Reviews</h4>
      <div className="kv">
        <div className="k">artifact_dir</div>
        <div className="v mono">{n.artifact_dir}</div>
        <div className="k">review_dir</div>
        <div className="v mono">{n.review_dir}</div>
      </div>
      {n.active_artifact ? (
        <div className="muted">
          active_artifact: <span className="mono">{n.active_artifact.path}</span> ({n.active_artifact.format})
        </div>
      ) : (
        <div className="muted">active_artifact: none</div>
      )}

      <h4>Last Error</h4>
      {n.last_error ? (
        <div className="muted">
          {n.last_error.created_at} {n.last_error.error_code}: {n.last_error.message}
        </div>
      ) : (
        <div className="muted">none</div>
      )}

      <h4>Last Review</h4>
      {n.last_review ? (
        <div className="muted">
          {n.last_review.created_at} score={n.last_review.total_score} action={n.last_review.action_required} summary={n.last_review.summary ?? "-"}
        </div>
      ) : (
        <div className="muted">none</div>
      )}
    </div>
  );
}


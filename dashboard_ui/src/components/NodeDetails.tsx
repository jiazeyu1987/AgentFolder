import React, { useEffect, useMemo, useState } from "react";
import type { ErrorsResp, GraphNode, TaskDetailsResp, TaskLlmCallsResp } from "../types";
import * as api from "../api";

type UiError = ErrorsResp["errors"][number];

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

type NodeDetailsProps = {
  node: GraphNode | null;
  planId: string | null;
  onRefresh?: () => void;
};

function groupErrorsForDisplay(errors: UiError[], opts: { primaryTaskTitle: string }) {
  // Group by (task_title, llm_call_id). When llm_call_id is null, treat as task-level error.
  const by = new Map<string, { taskTitle: string; llmCallId: string | null; items: UiError[] }>();
  for (const e of errors) {
    const taskTitle = e.task_title ?? opts.primaryTaskTitle;
    const llmCallId = e.llm_call_id ?? null;
    const key = `${taskTitle}::${llmCallId ?? "TASK"}`;
    const g = by.get(key) ?? { taskTitle, llmCallId, items: [] };
    g.items.push(e);
    by.set(key, g);
  }

  const groups = Array.from(by.values());
  for (const g of groups) g.items.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  groups.sort((a, b) => String(b.items[0]?.created_at ?? "").localeCompare(String(a.items[0]?.created_at ?? "")));

  // Per task_title, number review attempts (llm_call_id != null) by recency.
  const out: Array<{ header: string; taskTitle: string; isTaskLevel: boolean; items: UiError[] }> = [];
  const byTask = new Map<string, typeof groups>();
  for (const g of groups) {
    const arr = byTask.get(g.taskTitle) ?? [];
    arr.push(g);
    byTask.set(g.taskTitle, arr);
  }
  for (const [taskTitle, arr] of byTask.entries()) {
    const reviewGroups = arr.filter((x) => x.llmCallId);
    const taskLevelGroups = arr.filter((x) => !x.llmCallId);
    for (let i = 0; i < reviewGroups.length; i++) {
      const g = reviewGroups[i];
      const ts = g.items[0]?.created_at ?? "";
      out.push({ header: `${taskTitle} · Review #${i + 1} · ${ts}`, taskTitle, isTaskLevel: false, items: g.items });
    }
    for (const g of taskLevelGroups) {
      const ts = g.items[0]?.created_at ?? "";
      out.push({ header: `${taskTitle} · Task-level · ${ts}`, taskTitle, isTaskLevel: true, items: g.items });
    }
  }

  return out;
}

export default function NodeDetails(props: NodeDetailsProps) {
  const n = props.node;
  const planId = props.planId;
  const onRefresh = props.onRefresh;

  const [details, setDetails] = useState<TaskDetailsResp | null>(null);
  const [detailsErr, setDetailsErr] = useState<string>("");
  const [llm, setLlm] = useState<TaskLlmCallsResp | null>(null);
  const [llmErr, setLlmErr] = useState<string>("");
  const [errors, setErrors] = useState<ErrorsResp["errors"]>([]);
  const [errorsErr, setErrorsErr] = useState<string>("");

  const [resetAttempts, setResetAttempts] = useState<boolean>(false);
  const [resetAck, setResetAck] = useState<string | null>(null);
  const [resetBusy, setResetBusy] = useState<boolean>(false);

  useEffect(() => {
    setDetails(null);
    setDetailsErr("");
    setLlm(null);
    setLlmErr("");
    setErrors([]);
    setErrorsErr("");
    setResetAck(null);
    if (!n) return;

    api.getTaskDetails(n.task_id).then(setDetails).catch((e) => setDetailsErr(String(e)));
    api.getTaskLlmCalls(n.task_id, 50).then(setLlm).catch((e) => setLlmErr(String(e)));
    if (planId && (n.node_type === "CHECK" || n.status === "FAILED" || n.status === "BLOCKED")) {
      api
        .getErrors({ plan_id: planId, task_id: n.task_id, include_related: n.node_type !== "CHECK", limit: 200 })
        .then((r) => setErrors(r.errors))
        .catch((e) => setErrorsErr(String(e)));
    }
  }, [n?.task_id, planId]);

  const groups = useMemo(() => (llm ? groupByAgent(llm.calls) : []), [llm]);
  const errorsByLlmCallId = useMemo(() => {
    const m = new Map<string, UiError[]>();
    for (const e of errors) {
      if (!e.llm_call_id) continue;
      const arr = m.get(e.llm_call_id) ?? [];
      arr.push(e);
      m.set(e.llm_call_id, arr);
    }
    for (const arr of m.values()) {
      arr.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
    }
    return m;
  }, [errors]);

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
        <div className="v" style={{ fontWeight: 900, color: "#38bdf8" }}>
          {n.owner_agent_id}
        </div>
        <div className="k">blocked_reason</div>
        <div className="v">{n.blocked_reason ?? "-"}</div>
        <div className="k">attempts</div>
        <div className="v">{n.attempt_count}</div>
      </div>

      {n.status === "BLOCKED" ? (
        <div style={{ marginTop: 10, padding: 10, border: "1px solid #7c2d12", borderRadius: 10, background: "rgba(249,115,22,0.08)" }}>
          <div style={{ fontWeight: 900, color: "#fb923c" }}>Blocked Reason</div>
          <div className="muted" style={{ marginTop: 4 }}>
            {n.blocked_reason === "WAITING_INPUT"
              ? "Missing inputs are required before this node can run."
              : n.blocked_reason === "WAITING_EXTERNAL"
                ? "Automation stopped and needs human decision/fix (not waiting for a file by default)."
                : n.blocked_reason === "WAITING_SKILL"
                  ? "Waiting for an external skill or a skill failure to be resolved."
                  : "Blocked."}
          </div>
          {errorsErr ? <div className="muted" style={{ marginTop: 6 }}>load failed: {errorsErr}</div> : null}
          {!errorsErr && errors.length ? (
            (() => {
              const latest = errors[0];
              return (
                <div style={{ marginTop: 8 }}>
                  <div style={{ color: "#fb923c", fontWeight: 900 }}>
                    {latest.created_at} {latest.error_code ?? "ERROR"} {latest.agent ? `· ${latest.agent}` : ""} {latest.scope ? `· ${latest.scope}` : ""}
                  </div>
                  {latest.message ? <div style={{ color: "#fb923c" }}>{latest.message}</div> : null}
                  {latest.validator_error ? <div className="mono" style={{ color: "#fdba74" }}>{latest.validator_error}</div> : null}
                  {latest.hint ? <div className="muted" style={{ marginTop: 4 }}>next: {latest.hint}</div> : null}
                </div>
              );
            })()
          ) : !errorsErr ? (
            <div className="muted" style={{ marginTop: 6 }}>
              No error record found for this blocked node yet.
            </div>
          ) : null}
        </div>
      ) : null}

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
                  <div className="muted">
                    types: {Array.isArray(m.accepted_types) ? m.accepted_types.join(",") : String(m.accepted_types)}
                  </div>
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

      {n.node_type === "CHECK" || n.status === "FAILED" ? (
        <>
          <h4>Review Failures</h4>
          {errorsErr ? <div className="muted">load failed: {errorsErr}</div> : null}
          {!errors.length ? <div className="muted">none</div> : null}
          {errors.length ? (
            <div className="list">
              {groupErrorsForDisplay(errors, { primaryTaskTitle: n.title })
                .slice(0, 30)
                .map((g, idx) => (
                  <details key={idx} open={!g.isTaskLevel}>
                    <summary style={{ color: "#ef4444", fontWeight: 900 }}>{g.header}</summary>
                    <ul className="list">
                      {g.items.slice(0, 20).map((e, j) => (
                        <li key={j}>
                          <div style={{ color: "#ef4444", fontWeight: 900 }}>
                            {e.created_at} {e.error_code ?? "ERROR"} {e.agent ? `· ${e.agent}` : ""} {e.scope ? `· ${e.scope}` : ""}
                          </div>
                          {e.message ? <div style={{ color: "#ef4444" }}>{e.message}</div> : null}
                          {e.validator_error ? (
                            <div className="mono" style={{ color: "#fca5a5" }}>
                              {e.validator_error}
                            </div>
                          ) : null}
                          {e.hint ? <div className="muted">{e.hint}</div> : null}
                        </li>
                      ))}
                    </ul>
                  </details>
                ))}
            </div>
          ) : null}
        </>
      ) : null}

      {n.status === "FAILED" ? (
        <>
          <h4>Reset</h4>
          <div className="row">
            <label className="inline">
              reset-attempts
              <input type="checkbox" checked={resetAttempts} onChange={(e) => setResetAttempts(e.target.checked)} />
            </label>
            <div className="spacer" />
            <button
              onClick={async () => {
                if (!planId || resetBusy) return;
                setResetBusy(true);
                setResetAck("sending...");
                try {
                  const res = await api.resetFailed(planId, { reset_attempts: resetAttempts });
                  setResetAck(res.exit_code === 0 ? "done" : "failed");
                  onRefresh?.();
                } catch (e) {
                  setResetAck("failed");
                } finally {
                  setResetBusy(false);
                  setTimeout(() => setResetAck(null), 1000);
                }
              }}
              disabled={!planId || resetBusy}
            >
              Reset FAILED → READY
            </button>
          </div>
          {resetAck ? <div className="muted">reset: {resetAck}</div> : null}
        </>
      ) : null}

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
              {(() => {
                const isReviewCall = String(c.scope || "").includes("REVIEW");
                const extraDirect = errorsByLlmCallId.get(c.llm_call_id) ?? [];
                const extra = extraDirect;
                const hasErr = Boolean(c.error_code || c.validator_error || c.error_message || extra.length);
                const shouldShow = isReviewCall && hasErr;
                if (!shouldShow) return null;
                return (
                  <div style={{ marginTop: 8, padding: 8, border: "1px solid #7f1d1d", borderRadius: 8, background: "rgba(239,68,68,0.08)" }}>
                    <div style={{ color: "#ef4444", fontWeight: 900 }}>Error</div>
                    {c.error_code ? <div style={{ color: "#ef4444" }}>{c.error_code}</div> : null}
                    {c.error_message ? <div style={{ color: "#ef4444" }}>{c.error_message}</div> : null}
                    {c.validator_error ? <div className="mono" style={{ color: "#fca5a5" }}>{c.validator_error}</div> : null}
                    {extra.length ? (
                      <ul className="list" style={{ marginTop: 8 }}>
                        {extra.slice(0, 10).map((e, idx) => (
                          <li key={idx}>
                            <div style={{ color: "#ef4444", fontWeight: 900 }}>
                              {e.created_at} {e.error_code ?? "ERROR"} {e.scope ? `· ${e.scope}` : ""}
                            </div>
                            {e.message ? <div style={{ color: "#ef4444" }}>{e.message}</div> : null}
                            {e.validator_error ? <div className="mono" style={{ color: "#fca5a5" }}>{e.validator_error}</div> : null}
                            {e.hint ? <div className="muted">{e.hint}</div> : null}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                );
              })()}
            </details>
          ))}
        </details>
      ))}
    </div>
  );
}

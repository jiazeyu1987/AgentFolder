import React, { useMemo, useState } from "react";
import type { CreatePlanJobResp, LlmCallsQueryResp } from "../types";

function parseMetaAttempt(metaJson: string | null): { attempt: number; reviewAttempt: number } {
  if (!metaJson) return { attempt: 1, reviewAttempt: 1 };
  try {
    const obj = JSON.parse(metaJson);
    if (!obj || typeof obj !== "object") return { attempt: 1, reviewAttempt: 1 };
    const a = Number((obj as any).attempt ?? 1);
    const ra = Number((obj as any).review_attempt ?? 1);
    return { attempt: Number.isFinite(a) ? a : 1, reviewAttempt: Number.isFinite(ra) ? ra : 1 };
  } catch {
    return { attempt: 1, reviewAttempt: 1 };
  }
}

export default function CreatePlanProgress(props: {
  job: CreatePlanJobResp | null;
  timeline: LlmCallsQueryResp["calls"];
  onSelectPlanId: (planId: string) => void;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const planId = props.job?.plan_id ?? null;

  const head = useMemo(() => {
    if (!props.job) return null;
    const j = props.job;
    const phaseExtra = j.phase === "PLAN_REVIEW" ? ` (review_attempt=${j.review_attempt})` : "";
    return `attempt=${j.attempt} phase=${j.phase}${phaseExtra}`;
  }, [props.job]);

  return (
    <div className="panel">
      <h3>Create-Plan Progress</h3>
      {!props.job ? (
        <div className="muted">no active job</div>
      ) : (
        <>
          <div className="row">
            <div>
              <div>
                status: <span className="mono">{props.job.status}</span>
              </div>
              <div className="muted">{head}</div>
              <div className="muted">{props.job.hint}</div>
              {props.job.status !== "RUNNING" && props.job.exit_code != null ? (
                <div className="muted">
                  exit_code: <span className="mono">{String(props.job.exit_code)}</span>
                </div>
              ) : null}
              {props.job.status !== "RUNNING" && props.job.log_path ? (
                <div className="muted">
                  create-plan log: <span className="mono">{props.job.log_path}</span>
                </div>
              ) : null}
              {props.job.retry_reason ? <div className="muted">retry_reason: {props.job.retry_reason}</div> : null}
            </div>
            <div className="spacer" />
            {planId ? (
              <button
                onClick={() => {
                  if (!planId) return;
                  props.onSelectPlanId(planId);
                }}
              >
                Go Plan
              </button>
            ) : (
              <button disabled>Go Plan</button>
            )}
          </div>

          {props.job.last_llm_call ? (
            <div className="muted" style={{ marginTop: 8 }}>
              last: <span className="mono">{props.job.last_llm_call.scope}</span> @{" "}
              <span className="mono">{props.job.last_llm_call.created_at}</span>
              {props.job.last_llm_call.error_code ? (
                <>
                  {" "}
                  err=<span className="mono">{props.job.last_llm_call.error_code}</span>
                </>
              ) : null}
              {props.job.last_llm_call.validator_error ? (
                <>
                  {" "}
                  validator=<span className="mono">{String(props.job.last_llm_call.validator_error).slice(0, 120)}</span>
                </>
              ) : null}
            </div>
          ) : null}
        </>
      )}

      <h4 style={{ marginTop: 12 }}>LLM Timeline (PLAN_GEN / PLAN_REVIEW)</h4>
      {props.timeline.length === 0 ? (
        <div className="muted">no llm_calls yet</div>
      ) : (
        <div className="list">
          {props.timeline.map((c) => {
            const meta = parseMetaAttempt(c.meta_json);
            const isOpen = expandedId === c.llm_call_id;
            return (
              <div key={c.llm_call_id} className="listRow">
                <button className="link" onClick={() => setExpandedId(isOpen ? null : c.llm_call_id)}>
                  <span className="mono">{c.created_at}</span> · <span className="mono">{c.scope}</span> · attempt={meta.attempt}{" "}
                  {c.scope === "PLAN_REVIEW" ? `review_attempt=${meta.reviewAttempt}` : ""}{" "}
                  {c.error_code ? <span className="mono">err={c.error_code}</span> : null}
                </button>
                {isOpen ? (
                  <div className="details">
                    <div className="muted">validator_error: {c.validator_error || "-"}</div>
                    <div className="muted">error_message: {c.error_message || "-"}</div>
                    <details open>
                      <summary>Prompt</summary>
                      <pre className="pre">{c.prompt_text || ""}</pre>
                    </details>
                    <details>
                      <summary>Raw Response</summary>
                      <pre className="pre">{c.response_text || ""}</pre>
                    </details>
                    <details>
                      <summary>Parsed JSON</summary>
                      <pre className="pre">{c.parsed_json || ""}</pre>
                    </details>
                    <details>
                      <summary>Normalized JSON</summary>
                      <pre className="pre">{c.normalized_json || ""}</pre>
                    </details>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

import React, { useMemo } from "react";
import type { CreatePlanJobResp } from "../types";
import { formatLocalDateTime } from "../time";

export default function CreatePlanProgress(props: {
  job: CreatePlanJobResp | null;
  onSelectPlanId: (planId: string) => void;
}) {
  const planId = props.job?.plan_id ?? null;

  const head = useMemo(() => {
    if (!props.job) return null;
    const j = props.job;
    if (j.current_step && String(j.current_step).trim() && String(j.current_step).toUpperCase() !== "UNKNOWN") {
      return `step=${j.current_step} attempt=${j.attempt}`;
    }
    const stage = j.stage ? ` stage=${j.stage}${j.stage_attempt ? `(${j.stage_attempt})` : ""}` : "";
    const phaseExtra =
      j.phase === "PLAN_REVIEW"
        ? ` (review_attempt=${j.review_attempt})`
        : j.phase === "PLAN_RUBRIC"
          ? ` (rubric_attempt=${j.rubric_attempt ?? 1})`
          : "";
    return `attempt=${j.attempt}${stage} phase=${j.phase}${phaseExtra}`;
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
              {props.job.last_decision ? (
                <div className="muted">
                  decision:{" "}
                  <span className="mono">
                    {String(props.job.last_decision.payload?.why ?? "").slice(0, 40) || "DECISION"}
                  </span>{" "}
                  {String(props.job.last_decision.payload?.next ?? "").slice(0, 40) ? (
                    <>
                      → <span className="mono">{String(props.job.last_decision.payload?.next).slice(0, 40)}</span>
                    </>
                  ) : null}
                </div>
              ) : null}
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
              <span className="mono" title={props.job.last_llm_call.created_at}>
                {formatLocalDateTime(props.job.last_llm_call.created_at)}
              </span>
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
    </div>
  );
}

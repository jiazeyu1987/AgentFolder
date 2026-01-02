import React, { useEffect, useState } from "react";
import * as api from "../api";
import type { LlmCallsQueryResp, PromptFileResp } from "../types";

async function copyText(text: string) {
  await navigator.clipboard.writeText(text);
}

export default function ReviewSuggestionsPanel(props: { llmCallId: string | null }) {
  const [call, setCall] = useState<LlmCallsQueryResp["calls"][number] | null>(null);
  const [err, setErr] = useState<string>("");
  const [reviewNote, setReviewNote] = useState<PromptFileResp | null>(null);

  useEffect(() => {
    setCall(null);
    setErr("");
    setReviewNote(null);
    if (!props.llmCallId) return;
    api
      .getLlmCallsQuery({ llm_call_id: props.llmCallId, limit: 1 })
      .then((r) => setCall(r.calls[0] ?? null))
      .catch((e) => setErr(String(e)));
  }, [props.llmCallId]);

  async function loadReviewNote() {
    if (!call?.plan_review_attempt_path) return;
    const r = await api.getPromptFile(call.plan_review_attempt_path);
    setReviewNote(r);
  }

  return (
    <div className="panel">
      <h3>Suggestions</h3>
      {err ? <div className="muted">load failed: {err}</div> : null}
      {!props.llmCallId ? <div className="muted">switch to LLM Workflow and click a review node</div> : null}
      {props.llmCallId && !call ? <div className="muted">loading...</div> : null}
      {call ? (
        call.scope === "PLAN_REVIEW" ? (
          <>
            <div className="muted">
              plan_review_attempt: <span className="mono">{call.plan_review_attempt_path ?? "-"}</span>{" "}
              {call.plan_review_attempt_path ? (
                <>
                  <button onClick={loadReviewNote}>Load</button>{" "}
                  <button onClick={() => copyText(call.plan_review_attempt_path!)}>Copy Path</button>
                </>
              ) : (
                <span className="muted">(not generated)</span>
              )}
            </div>
            {reviewNote ? (
              <details open style={{ marginTop: 8 }}>
                <summary>attached to next xiaobo prompt (≤500 chars)</summary>
                <div className="row">
                  <button onClick={() => copyText(reviewNote.content)}>Copy</button>
                  <div className="spacer" />
                  {reviewNote.truncated ? <span className="muted">TRUNCATED</span> : null}
                </div>
                <pre className="pre">{reviewNote.content}</pre>
              </details>
            ) : null}
            {!call.plan_review_attempt_path ? <div className="muted" style={{ marginTop: 8 }}>no remediation note</div> : null}
          </>
        ) : (
          <div className="muted">select a PLAN_REVIEW node to see the remediation note</div>
        )
      ) : null}
    </div>
  );
}

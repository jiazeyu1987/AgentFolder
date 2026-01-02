import React, { useEffect, useMemo, useState } from "react";
import * as api from "../api";
import type { LlmCallsQueryResp, PromptFileResp } from "../types";

function safeJsonParse(s: string | null): any {
  if (!s) return null;
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

function extractReview(normalizedJson: string | null): any | null {
  const obj = safeJsonParse(normalizedJson);
  if (!obj || typeof obj !== "object") return null;
  // Common shapes in this repo:
  // - { schema_version, total_score, action_required, ... }
  // - { schema_version, review_result: { total_score, action_required, ... }, ... }
  if ((obj as any).total_score != null || (obj as any).action_required) return obj;
  if ((obj as any).review_result && typeof (obj as any).review_result === "object") return (obj as any).review_result;
  return null;
}

async function copyText(text: string) {
  await navigator.clipboard.writeText(text);
}

function preview(s: string | null, n = 160): string {
  const t = (s ?? "").replace(/\s+/g, " ").trim();
  if (!t) return "-";
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
}

export default function LLMCallDetails(props: { llmCallId: string | null }) {
  const [call, setCall] = useState<LlmCallsQueryResp["calls"][number] | null>(null);
  const [err, setErr] = useState<string>("");
  const [shared, setShared] = useState<PromptFileResp | null>(null);
  const [agentPrompt, setAgentPrompt] = useState<PromptFileResp | null>(null);
  const [reviewNote, setReviewNote] = useState<PromptFileResp | null>(null);

  useEffect(() => {
    setCall(null);
    setErr("");
    setShared(null);
    setAgentPrompt(null);
    setReviewNote(null);
    if (!props.llmCallId) return;
    api
      .getLlmCallsQuery({ llm_call_id: props.llmCallId, limit: 1 })
      .then((r) => setCall(r.calls[0] ?? null))
      .catch((e) => setErr(String(e)));
  }, [props.llmCallId]);

  const review = useMemo(() => extractReview(call?.normalized_json ?? null), [call?.normalized_json]);
  const suggestionSummary = useMemo(() => {
    if (!review) return "-";
    const sugs = (review as any).suggestions;
    if (!Array.isArray(sugs) || sugs.length === 0) return "-";
    const first = sugs
      .map((s: any) => (s && typeof s === "object" ? String(s.change ?? "").trim() : ""))
      .filter((x: string) => x)
      .slice(0, 2);
    return first.length ? first.join(" | ") : "-";
  }, [review]);

  async function loadShared() {
    if (!call?.shared_prompt_path) return;
    const r = await api.getPromptFile(call.shared_prompt_path);
    setShared(r);
  }

  async function loadAgent() {
    if (!call?.agent_prompt_path) return;
    const r = await api.getPromptFile(call.agent_prompt_path);
    setAgentPrompt(r);
  }

  async function loadReviewNote() {
    if (!call?.plan_review_attempt_path) return;
    const r = await api.getPromptFile(call.plan_review_attempt_path);
    setReviewNote(r);
  }

  if (!props.llmCallId) {
    return (
      <div className="panel">
        <h3>LLM Call</h3>
        <div className="muted">switch to LLM Workflow and click a node</div>
      </div>
    );
  }

  return (
    <div className="panel">
      <h3>LLM Call</h3>
      {err ? <div className="muted">load failed: {err}</div> : null}
      {!call ? <div className="muted">loading...</div> : null}
      {call ? (
        <>
          <div className="details">
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 18, fontWeight: 900, color: "#38bdf8" }}>{call.agent}</span>
              <span className="mono" style={{ fontSize: 12, color: "#94a3b8" }}>
                scope={call.scope} · {call.created_at}
              </span>
              {call.error_code ? (
                <span className="mono" style={{ fontSize: 12, color: "#fca5a5" }}>
                  err={call.error_code}
                </span>
              ) : null}
            </div>

            <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginTop: 6, flexWrap: "wrap" }}>
              <span style={{ fontSize: 16, fontWeight: 900, color: "#fbbf24" }}>
                Score {review ? String((review as any).total_score ?? "-") : "-"}
              </span>
              <span className="mono" style={{ fontSize: 12, color: "#cbd5e1" }}>
                action={review ? String((review as any).action_required ?? "-") : "-"}
              </span>
            </div>
            <div className="muted" style={{ marginTop: 6 }}>
              Suggestion: <span className="mono">{suggestionSummary}</span>
            </div>
            <div className="muted" style={{ marginTop: 6 }}>
              Final Prompt: <span className="mono">{preview(call.prompt_text)}</span>
            </div>
            <details style={{ marginTop: 6 }}>
              <summary>Final Prompt (expand)</summary>
              <div className="row">
                <button onClick={() => copyText(call.prompt_text ?? "")}>Copy</button>
              </div>
              <pre className="pre">{call.prompt_text ?? ""}</pre>
            </details>
            <div className="muted" style={{ marginTop: 6 }}>
              Raw Response: <span className="mono">{preview(call.response_text)}</span>
            </div>
            <details style={{ marginTop: 6 }}>
              <summary>Raw Response (expand)</summary>
              <div className="row">
                <button onClick={() => copyText(call.response_text ?? "")}>Copy</button>
              </div>
              <pre className="pre">{call.response_text ?? ""}</pre>
            </details>
            {call.validator_error ? <div className="muted" style={{ marginTop: 6 }}>validator_error: {String(call.validator_error).slice(0, 400)}</div> : null}
          </div>

          <h4 style={{ marginTop: 12 }}>Other</h4>
          <div className="muted">
            shared: <span className="mono">{call.shared_prompt_path ?? "-"}</span>{" "}
            {call.shared_prompt_path ? (
              <>
                <button onClick={loadShared}>Load</button> <button onClick={() => copyText(call.shared_prompt_path!)}>Copy Path</button>
              </>
            ) : null}
          </div>
          {shared ? (
            <details open>
              <summary>Shared Prompt Content</summary>
              <div className="row">
                <button onClick={() => copyText(shared.content)}>Copy</button>
                <div className="spacer" />
                {shared.truncated ? <span className="muted">TRUNCATED</span> : null}
              </div>
              <pre className="pre">{shared.content}</pre>
            </details>
          ) : null}

          <div className="muted" style={{ marginTop: 8 }}>
            agent: <span className="mono">{call.agent_prompt_path ?? "-"}</span>{" "}
            {call.agent_prompt_path ? (
              <>
                <button onClick={loadAgent}>Load</button> <button onClick={() => copyText(call.agent_prompt_path!)}>Copy Path</button>
              </>
            ) : null}
          </div>
          {agentPrompt ? (
            <details open>
              <summary>Agent Prompt Content</summary>
              <div className="row">
                <button onClick={() => copyText(agentPrompt.content)}>Copy</button>
                <div className="spacer" />
                {agentPrompt.truncated ? <span className="muted">TRUNCATED</span> : null}
              </div>
              <pre className="pre">{agentPrompt.content}</pre>
            </details>
          ) : null}

          {review ? (
            <details style={{ marginTop: 8 }}>
              <summary>Review details</summary>
              <div className="kv" style={{ marginTop: 8 }}>
                <div className="k">summary</div>
                <div className="v">{String((review as any).summary ?? "-")}</div>
              </div>
              {(review as any).dimension_scores ? (
                <details>
                  <summary>dimension_scores</summary>
                  <pre className="pre">{JSON.stringify((review as any).dimension_scores, null, 2)}</pre>
                </details>
              ) : null}
            </details>
          ) : null}

          {call.scope === "PLAN_REVIEW" ? (
            <>
              <h4>plan_review_attempt.md</h4>
              <div className="muted">
                path: <span className="mono">{call.plan_review_attempt_path ?? "-"}</span>{" "}
                {call.plan_review_attempt_path ? (
                  <>
                    <button onClick={loadReviewNote}>Load</button>{" "}
                    <button onClick={() => copyText(call.plan_review_attempt_path!)}>Copy Path</button>
                  </>
                ) : (
                  <span className="muted">(not generated yet)</span>
                )}
              </div>
              {reviewNote ? (
                <details open>
                  <summary>整改说明（≤500字）</summary>
                  <div className="row">
                    <button onClick={() => copyText(reviewNote.content)}>Copy</button>
                    <div className="spacer" />
                    {reviewNote.truncated ? <span className="muted">TRUNCATED</span> : null}
                  </div>
                  <pre className="pre">{reviewNote.content}</pre>
                </details>
              ) : null}
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

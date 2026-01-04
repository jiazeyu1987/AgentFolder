import React, { useEffect, useMemo, useState } from "react";
import * as api from "../api";
import type { LlmCallsQueryResp } from "../types";
import { formatLocalDateTime } from "../time";

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

function preview(s: string, n = 180): string {
  const t = s.replace(/\s+/g, " ").trim();
  if (!t) return "-";
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
}

export default function LLMCallDetails(props: { llmCallId: string | null }) {
  const [call, setCall] = useState<LlmCallsQueryResp["calls"][number] | null>(null);
  const [err, setErr] = useState<string>("");

  useEffect(() => {
    setCall(null);
    setErr("");
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

  const promptText = useMemo(() => (call?.prompt_text ?? "").replace(/\r\n/g, "\n"), [call?.prompt_text]);
  const responseText = useMemo(() => (call?.response_text ?? "").replace(/\r\n/g, "\n"), [call?.response_text]);

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
                scope={call.scope} · <span title={call.created_at}>{formatLocalDateTime(call.created_at)}</span>
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
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginTop: 6, flexWrap: "wrap" }}>
              <span style={{ fontSize: 16, fontWeight: 900, color: "#a3e635" }}>Suggestion</span>
              <span className="mono" style={{ fontSize: 16, fontWeight: 900, color: "#a3e635" }}>
                {suggestionSummary}
              </span>
            </div>

            <details style={{ marginTop: 10 }} open>
              <summary>LLM Input</summary>
              <div className="muted" style={{ marginTop: 6 }}>
                {preview(promptText)}
              </div>
              <div className="row" style={{ marginTop: 8 }}>
                <button onClick={() => copyText(promptText)}>Copy</button>
                <div className="spacer" />
              </div>
              <pre className="pre">{promptText}</pre>
            </details>

            <details style={{ marginTop: 8 }} open>
              <summary>LLM Output</summary>
              <div className="muted" style={{ marginTop: 6 }}>
                {preview(responseText)}
              </div>
              <div className="row" style={{ marginTop: 8 }}>
                <button onClick={() => copyText(responseText)}>Copy</button>
                <div className="spacer" />
              </div>
              <pre className="pre">{responseText}</pre>
            </details>
            {call.validator_error ? <div className="muted" style={{ marginTop: 6 }}>validator_error: {String(call.validator_error).slice(0, 400)}</div> : null}
          </div>
        </>
      ) : null}
    </div>
  );
}

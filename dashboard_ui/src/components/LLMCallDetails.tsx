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

export default function LLMCallDetails(props: { llmCallId: string | null }) {
  const [call, setCall] = useState<LlmCallsQueryResp["calls"][number] | null>(null);
  const [err, setErr] = useState<string>("");
  const [shared, setShared] = useState<PromptFileResp | null>(null);
  const [agentPrompt, setAgentPrompt] = useState<PromptFileResp | null>(null);

  useEffect(() => {
    setCall(null);
    setErr("");
    setShared(null);
    setAgentPrompt(null);
    if (!props.llmCallId) return;
    api
      .getLlmCallsQuery({ llm_call_id: props.llmCallId, limit: 1 })
      .then((r) => setCall(r.calls[0] ?? null))
      .catch((e) => setErr(String(e)));
  }, [props.llmCallId]);

  const review = useMemo(() => extractReview(call?.normalized_json ?? null), [call?.normalized_json]);

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
          <div className="kv">
            <div className="k">created_at</div>
            <div className="v mono">{call.created_at}</div>
            <div className="k">scope</div>
            <div className="v mono">{call.scope}</div>
            <div className="k">agent</div>
            <div className="v mono">{call.agent}</div>
            <div className="k">plan_id</div>
            <div className="v mono">{call.plan_id ?? "-"}</div>
            <div className="k">task_id</div>
            <div className="v mono">{call.task_id ?? "-"}</div>
            <div className="k">error</div>
            <div className="v mono">{call.error_code ?? "-"}</div>
          </div>

          {call.validator_error ? <div className="muted">validator_error: {String(call.validator_error).slice(0, 400)}</div> : null}
          {call.prompt_source_reason ? <div className="muted">prompt_source: {call.prompt_source_reason}</div> : null}

          <h4>Prompts</h4>
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

          <details open style={{ marginTop: 8 }}>
            <summary>Final Prompt (sent to model)</summary>
            <div className="row">
              <button onClick={() => copyText(call.prompt_text ?? "")}>Copy</button>
            </div>
            <pre className="pre">{call.prompt_text ?? ""}</pre>
          </details>

          <h4>Outputs</h4>
          <details open>
            <summary>Raw Response</summary>
            <div className="row">
              <button onClick={() => copyText(call.response_text ?? "")}>Copy</button>
            </div>
            <pre className="pre">{call.response_text ?? ""}</pre>
          </details>
          <details>
            <summary>Parsed JSON</summary>
            <pre className="pre">{call.parsed_json ?? ""}</pre>
          </details>
          <details>
            <summary>Normalized JSON</summary>
            <pre className="pre">{call.normalized_json ?? ""}</pre>
          </details>

          {review ? (
            <>
              <h4>Review</h4>
              <div className="kv">
                <div className="k">total_score</div>
                <div className="v mono">{String((review as any).total_score ?? "-")}</div>
                <div className="k">action_required</div>
                <div className="v mono">{String((review as any).action_required ?? "-")}</div>
                <div className="k">summary</div>
                <div className="v">{String((review as any).summary ?? "-")}</div>
              </div>
              {(review as any).dimension_scores ? (
                <details>
                  <summary>dimension_scores</summary>
                  <pre className="pre">{JSON.stringify((review as any).dimension_scores, null, 2)}</pre>
                </details>
              ) : null}
              {(review as any).suggestions ? (
                <details>
                  <summary>suggestions</summary>
                  <pre className="pre">{JSON.stringify((review as any).suggestions, null, 2)}</pre>
                </details>
              ) : null}
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}


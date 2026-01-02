import React, { useEffect, useMemo, useState } from "react";
import * as api from "../api";
import type { AuditResp, TopTasksResp } from "../types";

type Props = {
  selectedPlanId: string | null;
  createPlanJobId: string | null;
  onSelectPlanId: (planId: string) => void;
  onSelectLlmCallId: (llmCallId: string) => void;
  onSelectTaskId: (taskId: string) => void;
  onSetViewMode: (mode: "TASK" | "WORKFLOW" | "ERROR_ANALYSIS" | "AUDIT_LOG") => void;
};

function shortTs(ts: string) {
  const m = ts.match(/T(\d\d:\d\d:\d\d)/);
  return m ? m[1] : ts;
}

function parsePayload(payloadJson: string | null): Record<string, any> | null {
  if (!payloadJson) return null;
  try {
    const obj = JSON.parse(payloadJson);
    if (!obj || typeof obj !== "object") return null;
    return obj as any;
  } catch {
    return null;
  }
}

export default function AuditLogPage(props: Props) {
  const [tops, setTops] = useState<TopTasksResp["top_tasks"]>([]);
  const [topHash, setTopHash] = useState<string>("");
  const [category, setCategory] = useState<string>("");
  const [filterPlan, setFilterPlan] = useState<boolean>(false);
  const [events, setEvents] = useState<AuditResp["events"]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [err, setErr] = useState<string>("");

  useEffect(() => {
    api
      .getTopTasks(50)
      .then((r) => {
        setTops(r.top_tasks);
        if (!topHash && r.top_tasks.length) setTopHash(r.top_tasks[0].top_task_hash);
      })
      .catch((e) => setErr(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const query = useMemo(() => {
    return {
      top_task_hash: topHash || undefined,
      plan_id: filterPlan ? props.selectedPlanId || undefined : undefined,
      category: category.trim() ? category.trim() : undefined,
      limit: 300,
    };
  }, [topHash, category, filterPlan, props.selectedPlanId]);

  useEffect(() => {
    if (!topHash) return;
    let stopped = false;
    const tick = () =>
      api
        .getAudit(query)
        .then((r) => {
          if (!stopped) setEvents(r.events);
        })
        .catch((e) => {
          if (!stopped) setErr(String(e));
        });
    tick();
    const t = setInterval(tick, 1500);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [query, topHash]);

  const selected = useMemo(() => {
    if (!selectedId) return null;
    return events.find((e) => e.audit_id === selectedId) ?? null;
  }, [events, selectedId]);

  const selectedPayload = useMemo(() => {
    if (!selected) return null;
    return parsePayload(selected.payload_json);
  }, [selected]);

  return (
    <div className="panel" style={{ padding: 12, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div className="row" style={{ marginBottom: 10 }}>
        <div>
          <div className="title">动作日志</div>
          <div className="muted">记录用户/API/LLM/RunLoop 的关键动作（不存 LLM 内容，只存引用）。</div>
        </div>
        <div className="spacer" />
        <button className="pillBtn" onClick={() => props.onSetViewMode("TASK")}>
          Back
        </button>
      </div>

      <div className="row" style={{ gap: 8, marginBottom: 10 }}>
        <label className="inline">
          top_task
          <select value={topHash} onChange={(e) => setTopHash(e.target.value)} style={{ width: 280 }}>
            {tops.map((t) => (
              <option key={t.top_task_hash} value={t.top_task_hash}>
                {(t.top_task_title ?? "").slice(0, 50) || "(untitled)"} ({t.top_task_hash.slice(0, 6)})
              </option>
            ))}
          </select>
        </label>
        <label className="inline">
          category
          <input value={category} onChange={(e) => setCategory(e.target.value)} style={{ width: 160 }} placeholder="LLM_INPUT" />
        </label>
        <label className="inline" style={{ gap: 6 }}>
          plan_id
          <input type="checkbox" checked={filterPlan} onChange={(e) => setFilterPlan(e.target.checked)} disabled={!props.selectedPlanId} />
          <span className="muted mono">{props.selectedPlanId ? props.selectedPlanId.slice(0, 8) : "-"}</span>
        </label>
        <div className="spacer" />
        <button
          onClick={() => {
            api
              .getAudit(query)
              .then((r) => setEvents(r.events))
              .catch((e) => setErr(String(e)));
          }}
        >
          Refresh
        </button>
      </div>

      {err ? <div className="muted">error: {err}</div> : null}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, minHeight: 0, flex: 1 }}>
        <div style={{ minHeight: 0, overflow: "auto", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 10 }}>
          {events.length === 0 ? (
            <div className="muted" style={{ padding: 10 }}>
              No audit events.
            </div>
          ) : (
            events.map((e) => {
              const active = e.audit_id === selectedId;
              const ok = Number(e.ok) === 1;
              const msg = (e.message ?? "").replace(/\s+/g, " ").trim();
              const payload = parsePayload(e.payload_json);
              const retryKind = payload && typeof (payload as any).retry_kind === "string" ? ((payload as any).retry_kind as string) : "";
              return (
                <button
                  key={e.audit_id}
                  className="listRow link"
                  style={{
                    width: "100%",
                    padding: "8px 10px",
                    borderBottom: "1px solid rgba(255,255,255,0.06)",
                    background: active ? "rgba(56,189,248,0.08)" : "transparent",
                  }}
                  onClick={() => setSelectedId(e.audit_id)}
                >
                  <div className="row" style={{ gap: 8 }}>
                    <span className="mono">{shortTs(e.created_at)}</span>
                    <span className="mono">{e.category}</span>
                    <span className="mono">{e.action}</span>
                    {retryKind ? <span className="mono" style={{ color: "#fca5a5" }}>retry={retryKind}</span> : null}
                    <span className="spacer" />
                    <span className="mono" style={{ color: ok ? "#86efac" : "#fca5a5" }}>
                      {ok ? "OK" : "FAIL"}
                    </span>
                  </div>
                  <div className="muted" style={{ marginTop: 4, textAlign: "left" }}>
                    {msg ? msg.slice(0, 140) : "(no message)"}
                  </div>
                </button>
              );
            })
          )}
        </div>

        <div style={{ minHeight: 0, overflow: "auto", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 10, padding: 10 }}>
          {!selected ? (
            <div className="muted">Select an event.</div>
          ) : (
            <>
              <div className="kv">
                <div className="k">time</div>
                <div className="v mono">{selected.created_at}</div>
                <div className="k">category</div>
                <div className="v mono">{selected.category}</div>
                <div className="k">action</div>
                <div className="v mono">{selected.action}</div>
                <div className="k">agent</div>
                <div className="v mono">{(selectedPayload?.agent as string) ?? "-"}</div>
                <div className="k">task_id</div>
                <div className="v mono">{selected.task_id ?? "-"}</div>
                <div className="k">error_code</div>
                <div className="v mono">{(selectedPayload?.error_code as string) ?? "-"}</div>
                <div className="k">retry_kind</div>
                <div className="v mono">{(selectedPayload?.retry_kind as string) ?? "-"}</div>
                <div className="k">status</div>
                <div className="v mono">
                  {(selected.status_before ?? "-") + " -> " + (selected.status_after ?? "-")}
                </div>
              </div>
              {selectedPayload?.retry_reason ? (
                <>
                  <div className="muted" style={{ marginTop: 8 }}>
                    retry_reason
                  </div>
                  <pre className="pre">{String(selectedPayload.retry_reason)}</pre>
                </>
              ) : null}
              {selected.payload_json ? (
                <details open style={{ marginTop: 8 }}>
                  <summary className="muted">payload_json</summary>
                  <pre className="pre">{selected.payload_json}</pre>
                </details>
              ) : null}
              {selected.message ? (
                <>
                  <div className="muted" style={{ marginTop: 8 }}>
                    message
                  </div>
                  <pre className="pre">{selected.message}</pre>
                </>
              ) : null}

              <div className="row" style={{ marginTop: 10, gap: 8 }}>
                {selected.llm_call_id ? (
                  <button
                    onClick={() => {
                      props.onSetViewMode("WORKFLOW");
                      props.onSelectLlmCallId(selected.llm_call_id!);
                    }}
                  >
                    Open In Workflow
                  </button>
                ) : null}
                {selected.task_id ? (
                  <button
                    onClick={() => {
                      props.onSetViewMode("TASK");
                      props.onSelectTaskId(selected.task_id!);
                    }}
                  >
                    Open Task
                  </button>
                ) : null}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

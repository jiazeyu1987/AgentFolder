import React, { useEffect, useMemo, useState } from "react";
import * as api from "../api";
import type { ErrorsResp } from "../types";

type Props = {
  title?: string;
  planId: string | null;
  followCreatePlanWithoutPlanId?: boolean;
  onSelectLlmCallId?: (llmCallId: string) => void;
  onSelectTaskId?: (taskId: string) => void;
  onSelectPlanId?: (planId: string) => void;
  onSetViewMode?: (mode: "TASK" | "WORKFLOW") => void;
};

function shortTs(ts: string) {
  // 2026-01-02T07:51:04Z -> 07:51:04
  const m = ts.match(/T(\d\d:\d\d:\d\d)/);
  return m ? m[1] : ts;
}

export default function ErrorsPanel({
  title = "Errors",
  planId,
  followCreatePlanWithoutPlanId,
  onSelectLlmCallId,
  onSelectTaskId,
  onSelectPlanId,
  onSetViewMode,
}: Props) {
  const [errors, setErrors] = useState<ErrorsResp["errors"]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [errText, setErrText] = useState<string>("");

  const query = useMemo(() => {
    if (planId) return { plan_id: planId, limit: 200 };
    if (followCreatePlanWithoutPlanId) return { plan_id_missing: true, limit: 200 };
    return null;
  }, [planId, followCreatePlanWithoutPlanId]);

  useEffect(() => {
    if (!query) {
      setErrors([]);
      return;
    }
    let stopped = false;
    const tick = () =>
      api
        .getErrors(query)
        .then((r) => {
          if (!stopped) setErrors(r.errors);
        })
        .catch((e) => {
          if (!stopped) setErrText(String(e));
        });
    tick();
    const t = setInterval(tick, 1500);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [query]);

  const selected = useMemo(() => {
    if (!selectedKey) return null;
    return errors.find((e) => `${e.source}:${e.llm_call_id ?? ""}:${e.task_id ?? ""}:${e.created_at}` === selectedKey) ?? null;
  }, [errors, selectedKey]);

  return (
    <div className="panel" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div className="row" style={{ marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>{title}</h3>
        <div className="spacer" />
        <div className="muted mono">{errors.length ? `${errors.length}` : "-"}</div>
      </div>

      {!query ? (
        <div className="muted">Select a plan to view errors.</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, minHeight: 0 }}>
          <div style={{ minHeight: 0, overflow: "auto", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 10 }}>
            {errors.length === 0 ? (
              <div className="muted" style={{ padding: 10 }}>
                No errors.
              </div>
            ) : (
              errors.map((e) => {
                const key = `${e.source}:${e.llm_call_id ?? ""}:${e.task_id ?? ""}:${e.created_at}`;
                const active = key === selectedKey;
                const label = `${shortTs(e.created_at)} ${e.source}${e.scope ? `/${e.scope}` : ""}${e.agent ? `/${e.agent}` : ""}`;
                const code = e.error_code ?? "-";
                const msg = (e.message ?? "").replace(/\s+/g, " ").trim();
                return (
                  <button
                    key={key}
                    className="listRow link"
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      borderBottom: "1px solid rgba(255,255,255,0.06)",
                      background: active ? "rgba(56,189,248,0.08)" : "transparent",
                    }}
                    onClick={() => setSelectedKey(key)}
                    title={msg}
                  >
                    <div className="row" style={{ gap: 8 }}>
                      <span className="mono" style={{ fontSize: 12 }}>
                        {label}
                      </span>
                      <span className="spacer" />
                      <span className="mono" style={{ fontSize: 12, color: "#fca5a5" }}>
                        {code}
                      </span>
                    </div>
                    <div className="muted" style={{ marginTop: 4, textAlign: "left" }}>
                      {e.task_title ? `${e.task_title}: ` : ""}
                      {msg ? msg.slice(0, 140) : "(no message)"}
                    </div>
                  </button>
                );
              })
            )}
          </div>

          <div style={{ minHeight: 0, overflow: "auto", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 10, padding: 10 }}>
            {!selected ? (
              <div className="muted">{errText ? `Load error: ${errText}` : "Click an error to inspect."}</div>
            ) : (
              <>
                <div className="kv">
                  <div className="k">time</div>
                  <div className="v mono">{selected.created_at}</div>
                  <div className="k">source</div>
                  <div className="v mono">{selected.source}</div>
                  <div className="k">scope</div>
                  <div className="v mono">{selected.scope ?? "-"}</div>
                  <div className="k">agent</div>
                  <div className="v mono">{selected.agent ?? "-"}</div>
                  <div className="k">task</div>
                  <div className="v">{selected.task_title ?? "-"}</div>
                  <div className="k">error_code</div>
                  <div className="v mono">{selected.error_code ?? "-"}</div>
                </div>
                {selected.message ? (
                  <>
                    <div className="muted">message</div>
                    <pre className="pre">{selected.message}</pre>
                  </>
                ) : null}
                {selected.hint ? (
                  <>
                    <div className="muted" style={{ marginTop: 8 }}>
                      hint
                    </div>
                    <pre className="pre">{selected.hint}</pre>
                  </>
                ) : null}
                {selected.validator_error ? (
                  <>
                    <div className="muted" style={{ marginTop: 8 }}>
                      validator_error
                    </div>
                    <pre className="pre">{selected.validator_error}</pre>
                  </>
                ) : null}
                <div className="row" style={{ marginTop: 10, gap: 8 }}>
                  {selected.plan_id && onSelectPlanId ? (
                    <button
                      onClick={() => {
                        onSelectPlanId(selected.plan_id!);
                      }}
                    >
                      Switch Plan
                    </button>
                  ) : null}
                  {selected.llm_call_id && onSelectLlmCallId ? (
                    <button
                      onClick={() => {
                        onSetViewMode?.("WORKFLOW");
                        onSelectLlmCallId(selected.llm_call_id!);
                      }}
                    >
                      Open In Workflow
                    </button>
                  ) : null}
                  {selected.task_id && onSelectTaskId ? (
                    <button
                      onClick={() => {
                        onSetViewMode?.("TASK");
                        onSelectTaskId(selected.task_id!);
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
      )}
    </div>
  );
}


import React, { useEffect, useMemo, useState } from "react";
import * as api from "../api";
import type { CreatePlanJobResp, PromptFileResp } from "../types";
import ErrorsPanel from "./ErrorsPanel";

type Props = {
  jobId: string | null;
  selectedPlanId: string | null;
  onSelectPlanId: (planId: string) => void;
  onSelectLlmCallId: (llmCallId: string) => void;
  onSelectTaskId: (taskId: string) => void;
  onSetViewMode: (mode: "TASK" | "WORKFLOW" | "ERROR_ANALYSIS") => void;
};

export default function ErrorAnalysisPage(props: Props) {
  const [job, setJob] = useState<CreatePlanJobResp | null>(null);
  const [jobLog, setJobLog] = useState<PromptFileResp | null>(null);
  const [err, setErr] = useState<string>("");

  useEffect(() => {
    if (!props.jobId) {
      setJob(null);
      setJobLog(null);
      return;
    }
    let stopped = false;
    const tick = async () => {
      try {
        const j = await api.getJob(props.jobId!);
        if (stopped) return;
        setJob(j);
        // Only load log after job ends or if exit_code exists.
        if (j.status !== "RUNNING" || j.exit_code != null) {
          try {
            const log = await api.getJobLog(props.jobId!, 200_000);
            if (!stopped) setJobLog(log);
          } catch (e) {
            if (!stopped) setJobLog(null);
          }
        }
      } catch (e) {
        if (!stopped) setErr(String(e));
      }
    };
    tick();
    const t = setInterval(tick, 1500);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [props.jobId]);

  const effectivePlanId = useMemo(() => {
    return job?.plan_id ?? props.selectedPlanId ?? null;
  }, [job?.plan_id, props.selectedPlanId]);

  return (
    <div className="panel" style={{ padding: 12, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div className="row" style={{ marginBottom: 10 }}>
        <div>
          <div className="title">错误分析</div>
          <div className="muted">用于定位 create-plan 中断原因（exit_code + create-plan log + errors）。</div>
        </div>
        <div className="spacer" />
        <button className="pillBtn" onClick={() => props.onSetViewMode("TASK")}>
          Back
        </button>
      </div>

      {!props.jobId ? (
        <div className="muted">没有 create-plan job。先点击 Create Plan 再来这里。</div>
      ) : err ? (
        <div className="muted">加载失败：{err}</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 10, minHeight: 0, flex: 1 }}>
          <div className="panel" style={{ minHeight: 0, overflow: "auto" }}>
            <h3 style={{ marginTop: 0 }}>Create-Plan Job</h3>
            {!job ? (
              <div className="muted">loading...</div>
            ) : (
              <>
                <div className="kv">
                  <div className="k">job_id</div>
                  <div className="v mono">{job.job_id}</div>
                  <div className="k">status</div>
                  <div className="v mono">{job.status}</div>
                  <div className="k">plan_id</div>
                  <div className="v mono">{job.plan_id ?? "-"}</div>
                  <div className="k">phase</div>
                  <div className="v mono">
                    attempt={job.attempt} phase={job.phase}
                    {job.phase === "PLAN_REVIEW" ? ` review_attempt=${job.review_attempt}` : ""}
                  </div>
                  <div className="k">exit_code</div>
                  <div className="v mono">{job.exit_code ?? "-"}</div>
                </div>
                {job.hint ? <div className="muted">{job.hint}</div> : null}
                {job.log_path ? (
                  <div className="muted">
                    log_path: <span className="mono">{job.log_path}</span>
                  </div>
                ) : null}
                <h4 style={{ marginTop: 12 }}>create_plan.log</h4>
                {jobLog ? (
                  <pre className="pre">{jobLog.content}</pre>
                ) : job?.status === "RUNNING" ? (
                  <div className="muted">job still running; log will appear after it ends.</div>
                ) : (
                  <div className="muted">no log available.</div>
                )}
              </>
            )}
          </div>

          <div style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column", gap: 10 }}>
            <ErrorsPanel
              title="Errors (Plan)"
              planId={effectivePlanId}
              followCreatePlanWithoutPlanId={job?.status === "RUNNING" && !job?.plan_id}
              onSelectPlanId={(pid) => props.onSelectPlanId(pid)}
              onSetViewMode={(m) => props.onSetViewMode(m)}
              onSelectLlmCallId={(id) => props.onSelectLlmCallId(id)}
              onSelectTaskId={(id) => props.onSelectTaskId(id)}
            />
            <div className="panel">
              <div className="muted">
                提示：优先看 create_plan.log 的最后几行；同时在 Errors 里点 “Open In Workflow” 查看对应的 LLM 输入输出。
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


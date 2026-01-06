import React, { useEffect, useMemo, useState } from "react";
import * as api from "./api";
import type { ConfigResp, GraphNode, PlansResp } from "./types";
import ControlPanel from "./components/ControlPanel";
import TaskGraph from "./components/TaskGraph";
import NodeDetails from "./components/NodeDetails";
import LLMWorkflowGraph from "./components/LLMWorkflowGraph";
import LLMCallDetails from "./components/LLMCallDetails";
import ReviewSuggestionsPanel from "./components/ReviewSuggestionsPanel";
import ErrorAnalysisPage from "./components/ErrorAnalysisPage";
import AuditLogPage from "./components/AuditLogPage";
import { usePlanData } from "./hooks/usePlanData";
import { useWorkflowData } from "./hooks/useWorkflowData";
import { useCreatePlanJob } from "./hooks/useCreatePlanJob";

export default function App() {
  const [config, setConfig] = useState<ConfigResp | null>(null);
  const [plans, setPlans] = useState<PlansResp["plans"]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [autoSelectPlanFromJob, setAutoSelectPlanFromJob] = useState<boolean>(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [topTask, setTopTask] = useState<string>("");
  const [logText, setLogText] = useState<string>("");
  const [createPlanJobId, setCreatePlanJobId] = useState<string | null>(() => localStorage.getItem("create_plan_job_id"));
  const createPlanJobState = useCreatePlanJob({
    jobId: createPlanJobId,
    enabled: Boolean(createPlanJobId),
    pollMs: 800,
    onJobNotFound: () => {
      setCreatePlanJobId(null);
      localStorage.removeItem("create_plan_job_id");
    },
  });
  const createPlanJob = createPlanJobState.data;
  const [viewMode, setViewMode] = useState<"TASK" | "WORKFLOW" | "ERROR_ANALYSIS" | "AUDIT_LOG">("TASK");
  const [selectedLlmCallId, setSelectedLlmCallId] = useState<string | null>(null);
  const [workflowScopes, setWorkflowScopes] = useState<string>("PLAN_RUBRIC,PLAN_GEN,PLAN_REVIEW");
  const [workflowAgent, setWorkflowAgent] = useState<string>("");
  const [workflowOnlyErrors, setWorkflowOnlyErrors] = useState<boolean>(false);

  const selectedPlanTitle = useMemo(() => {
    if (!selectedPlanId) return null;
    const p = plans.find((x) => x.plan_id === selectedPlanId);
    return p?.title ?? null;
  }, [plans, selectedPlanId]);

  const selectedTopTaskHash = useMemo(() => {
    if (!selectedPlanId) return null;
    const p = plans.find((x) => x.plan_id === selectedPlanId);
    return p?.top_task_hash ?? null;
  }, [plans, selectedPlanId]);

  function log(s: string) {
    setLogText((prev) => (prev ? prev + "\n\n" + s : s));
  }

  const planData = usePlanData(selectedPlanId, { enabled: Boolean(selectedPlanId), pollMs: viewMode === "TASK" ? 2000 : null });
  const workflowState = useWorkflowData({
    enabled: viewMode === "WORKFLOW",
    pollMs: 1200,
    createPlanJobId,
    createPlanRunning: createPlanJob?.status === "RUNNING",
    selectedPlanId,
    selectedTopTaskHash,
    scopes: workflowScopes,
    agent: workflowAgent,
    onlyErrors: workflowOnlyErrors,
  });

  async function refresh() {
    const [cfg, pls] = await Promise.all([api.getConfig(), api.getPlans()]);
    setConfig(cfg);
    setPlans(pls.plans);
    const pid =
      selectedPlanId && pls.plans.some((p) => p.plan_id === selectedPlanId)
        ? selectedPlanId
        : pls.plans.length
          ? pls.plans[0].plan_id
          : null;
    setSelectedPlanId(pid);
    if (!pid) setSelectedTaskId(null);
  }

  useEffect(() => {
    refresh().catch((e) => log(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Polling moved to hooks (usePlanData/useWorkflowData). TaskGraph no longer re-layouts on status-only updates.

  useEffect(() => {
    if (!createPlanJobId) return;
    localStorage.setItem("create_plan_job_id", createPlanJobId);
  }, [createPlanJobId]);

  useEffect(() => {
    const j = createPlanJob;
    if (!autoSelectPlanFromJob || !j) return;
    if (j.status !== "RUNNING" && j.plan_id) {
      setSelectedPlanId(j.plan_id);
      setAutoSelectPlanFromJob(false);
    }
  }, [autoSelectPlanFromJob, createPlanJob]);

  useEffect(() => {
    const g = planData.data?.graph;
    if (!g) return;
    if (selectedTaskId && !g.nodes.find((n) => n.task_id === selectedTaskId)) {
      setSelectedTaskId(null);
    }
  }, [planData.data?.graph, selectedTaskId]);

  const selectedNode: GraphNode | null = useMemo(() => {
    const g = planData.data?.graph;
    if (!g || !selectedTaskId) return null;
    return g.nodes.find((n) => n.task_id === selectedTaskId) ?? null;
  }, [planData.data?.graph, selectedTaskId]);

  const graph = planData.data?.graph ?? null;
  const snapshot = planData.data?.snapshot ?? null;

  const headerTitle = snapshot?.plan?.title ?? graph?.plan.title ?? "No Plan";
  const headerPlanId = snapshot?.plan?.plan_id ?? graph?.plan.plan_id ?? "";
  const planDone = Boolean(snapshot?.summary?.is_done);
  const reasonHead = snapshot?.reasons?.length ? String(snapshot.reasons[0].code) : "";
  const nextCmd =
    snapshot && (snapshot.report as any)?.next_steps && Array.isArray((snapshot.report as any).next_steps) && (snapshot.report as any).next_steps.length
      ? String((snapshot.report as any).next_steps[0]?.cmd || "")
      : "";

  return (
    <div className="layout">
      <div className="left">
        <ControlPanel
          config={config}
          plans={plans}
          selectedPlanId={selectedPlanId}
          onSelectPlanId={(v) => {
            setSelectedPlanId(v);
            setAutoSelectPlanFromJob(false);
          }}
          onCreatePlanJobId={(jobId) => {
            setCreatePlanJobId(jobId);
            setAutoSelectPlanFromJob(true);
          }}
          onOpenErrorAnalysis={() => {
            setViewMode("ERROR_ANALYSIS");
          }}
          onOpenAuditLog={() => {
            setViewMode("AUDIT_LOG");
          }}
          topTask={topTask}
          onTopTaskChange={setTopTask}
          onRefresh={() => refresh().catch((e) => log(String(e)))}
          onLog={log}
        />
      </div>
      <div className="center">
        <div className="panel header">
          <div>
            <div className="title">{headerTitle}</div>
            <div className="muted mono">{headerPlanId}</div>
          </div>
          <div className="muted">
            plan: <span className="mono">{planDone ? "DONE" : "NOT_DONE"}</span>
            {reasonHead ? (
              <>
                {" "}
                · reason: <span className="mono">{reasonHead}</span>
              </>
            ) : null}
            {nextCmd ? (
              <>
                {" "}
                · next: <span className="mono">{nextCmd}</span>
              </>
            ) : null}
            {" "}
            · runner: <span className="mono">{graph?.running.task_id ? `Running(${graph.running.task_id.slice(0, 8)})` : "Pause"}</span>
          </div>
          <div className="spacer" />
          <div className="row" style={{ gap: 8 }}>
            <button className={viewMode === "TASK" ? "pillBtn active" : "pillBtn"} onClick={() => setViewMode("TASK")}>
              Task Graph
            </button>
            <button className={viewMode === "WORKFLOW" ? "pillBtn active" : "pillBtn"} onClick={() => setViewMode("WORKFLOW")}>
              LLM Workflow
            </button>
            <button className={viewMode === "ERROR_ANALYSIS" ? "pillBtn active" : "pillBtn"} onClick={() => setViewMode("ERROR_ANALYSIS")}>
              错误分析
            </button>
            <button className={viewMode === "AUDIT_LOG" ? "pillBtn active" : "pillBtn"} onClick={() => setViewMode("AUDIT_LOG")}>
              动作日志
            </button>
          </div>
        </div>
        {viewMode === "AUDIT_LOG" ? (
          <AuditLogPage
            selectedPlanId={selectedPlanId}
            createPlanJobId={createPlanJobId}
            onSelectPlanId={(pid) => setSelectedPlanId(pid)}
            onSelectLlmCallId={(id) => {
              setViewMode("WORKFLOW");
              setSelectedLlmCallId(id);
            }}
            onSelectTaskId={(id) => {
              setViewMode("TASK");
              setSelectedTaskId(id);
            }}
            onSetViewMode={(m) => setViewMode(m)}
          />
        ) : viewMode === "ERROR_ANALYSIS" ? (
          <ErrorAnalysisPage
            jobId={createPlanJobId}
            selectedPlanId={selectedPlanId}
            onSelectPlanId={(pid) => setSelectedPlanId(pid)}
            onSelectLlmCallId={(id) => {
              setViewMode("WORKFLOW");
              setSelectedLlmCallId(id);
            }}
            onSelectTaskId={(id) => {
              setViewMode("TASK");
              setSelectedTaskId(id);
            }}
            onSetViewMode={(m) => setViewMode(m)}
          />
        ) : viewMode === "WORKFLOW" ? (
          <div className="panel" style={{ padding: 12, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div className="row" style={{ gap: 8, marginBottom: 10 }}>
              <div style={{ fontWeight: 900, color: "#a855f7" }}>{selectedPlanTitle ? `Plan: ${selectedPlanTitle}` : "Plan: -"}</div>
              <div className="spacer" />
              <label className="inline">
                scopes
                <input value={workflowScopes} onChange={(e) => setWorkflowScopes(e.target.value)} style={{ width: 220 }} />
              </label>
              <label className="inline">
                agent
                <input value={workflowAgent} onChange={(e) => setWorkflowAgent(e.target.value)} style={{ width: 120 }} />
              </label>
              <label className="inline">
                only_errors
                <input type="checkbox" checked={workflowOnlyErrors} onChange={(e) => setWorkflowOnlyErrors(e.target.checked)} />
              </label>
              <button
                onClick={() => {
                  workflowState.refresh();
                }}
              >
                Refresh
              </button>
            </div>
            <div style={{ flex: 1, minHeight: 0 }}>
              {workflowState.data ? (
                <LLMWorkflowGraph workflow={workflowState.data} onSelectCall={(id) => setSelectedLlmCallId(id)} />
              ) : workflowState.error ? (
                <div className="muted">
                  workflow error: {workflowState.error}
                  <div style={{ marginTop: 8 }}>
                    <button
                      onClick={() => {
                        workflowState.refresh();
                      }}
                    >
                      Retry
                    </button>
                  </div>
                </div>
              ) : (
                <div className="muted">loading workflow...</div>
              )}
            </div>
          </div>
        ) : (
          <div className="panel graphWrap" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div className="row" style={{ gap: 8, padding: "10px 10px 0 10px" }}>
              <div style={{ fontWeight: 900 }}>Task Graph</div>
              <div className="spacer" />
              <button
                onClick={() => {
                  refresh().catch((e) => log(String(e)));
                  planData.refresh();
                }}
              >
                Refresh
              </button>
            </div>
            <div style={{ flex: 1, minHeight: 0 }}>
              {graph ? (
                <TaskGraph nodes={graph.nodes} edges={graph.edges} onSelectNode={(id) => setSelectedTaskId(id)} />
              ) : planData.error ? (
                <div className="muted">
                  graph error: {planData.error}
                  <div style={{ marginTop: 8 }}>
                    <button
                      onClick={() => {
                        refresh().catch((e) => log(String(e)));
                        planData.refresh();
                      }}
                    >
                      Retry
                    </button>
                  </div>
                </div>
              ) : planData.loading ? (
                <div className="muted">loading graph...</div>
              ) : (
                <div className="muted">no graph</div>
              )}
            </div>
          </div>
        )}
      </div>
      <div className="right">
        <ReviewSuggestionsPanel llmCallId={viewMode === "WORKFLOW" ? selectedLlmCallId : null} />
        {viewMode === "WORKFLOW" ? (
          <LLMCallDetails llmCallId={selectedLlmCallId} />
        ) : viewMode === "TASK" ? (
          <NodeDetails
            node={selectedNode}
            planId={selectedPlanId}
            snapshot={snapshot}
            inputsDir={(config as any)?.paths?.inputs_dir ?? null}
            onRefresh={() => refresh().catch((e) => log(String(e)))}
          />
        ) : null}
      </div>
    </div>
  );
}

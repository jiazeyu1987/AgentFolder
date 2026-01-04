import React, { useEffect, useMemo, useState } from "react";
import * as api from "./api";
import type { ConfigResp, CreatePlanJobResp, GraphNode, GraphV1, PlansResp } from "./types";
import ControlPanel from "./components/ControlPanel";
import TaskGraph from "./components/TaskGraph";
import NodeDetails from "./components/NodeDetails";
import LLMWorkflowGraph from "./components/LLMWorkflowGraph";
import LLMCallDetails from "./components/LLMCallDetails";
import ReviewSuggestionsPanel from "./components/ReviewSuggestionsPanel";
import ErrorAnalysisPage from "./components/ErrorAnalysisPage";
import AuditLogPage from "./components/AuditLogPage";
import type { WorkflowResp } from "./types";

export default function App() {
  const [config, setConfig] = useState<ConfigResp | null>(null);
  const [plans, setPlans] = useState<PlansResp["plans"]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [autoSelectPlanFromJob, setAutoSelectPlanFromJob] = useState<boolean>(false);
  const [graph, setGraph] = useState<GraphV1 | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [topTask, setTopTask] = useState<string>("");
  const [logText, setLogText] = useState<string>("");
  const [createPlanJobId, setCreatePlanJobId] = useState<string | null>(() => localStorage.getItem("create_plan_job_id"));
  const [createPlanJob, setCreatePlanJob] = useState<CreatePlanJobResp | null>(null);
  const [viewMode, setViewMode] = useState<"TASK" | "WORKFLOW" | "ERROR_ANALYSIS" | "AUDIT_LOG">("TASK");
  const [workflow, setWorkflow] = useState<WorkflowResp | null>(null);
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
    if (pid) {
      const g = await api.getGraph(pid);
      setGraph(g);
      if (selectedTaskId && !g.nodes.find((n) => n.task_id === selectedTaskId)) {
        setSelectedTaskId(null);
      }
    } else {
      setGraph(null);
      setSelectedTaskId(null);
    }
  }

  useEffect(() => {
    refresh().catch((e) => log(String(e)));
    // polling: lightweight graph refresh
    const t = setInterval(() => {
      if (!selectedPlanId) return;
      api
        .getGraph(selectedPlanId)
        .then((g) => setGraph(g))
        .catch(() => {});
    }, 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (viewMode !== "WORKFLOW") return;
    const t = setInterval(() => {
      // When create-plan is running, follow its workflow only if the user hasn't switched to a different plan.
      const followJob = createPlanJob?.status === "RUNNING" && (!selectedPlanId || selectedPlanId === createPlanJob?.plan_id);
      const pidFallback = followJob ? undefined : selectedPlanId ?? undefined;
      const topHash = followJob ? undefined : selectedTopTaskHash ?? undefined;
      (async () => {
        try {
          if (followJob && createPlanJobId) {
            const w = await api.getWorkflow({
              job_id: createPlanJobId,
              plan_id_missing: false,
              scopes: workflowScopes.trim() ? workflowScopes : undefined,
              agent: workflowAgent.trim() ? workflowAgent : undefined,
              only_errors: workflowOnlyErrors,
              limit: 200,
            });
            setWorkflow(w);
            return;
          }

          if (topHash) {
            const w = await api.getWorkflow({
              top_task_hash: topHash,
              plan_id_missing: false,
              scopes: workflowScopes.trim() ? workflowScopes : undefined,
              agent: workflowAgent.trim() ? workflowAgent : undefined,
              only_errors: workflowOnlyErrors,
              limit: 200,
            });
            if ((w.returned_rows ?? w.nodes.length) > 0) {
              setWorkflow(w);
              return;
            }
          }

          if (pidFallback) {
            const w2 = await api.getWorkflow({
              plan_id: pidFallback,
              plan_id_missing: false,
              scopes: workflowScopes.trim() ? workflowScopes : undefined,
              agent: workflowAgent.trim() ? workflowAgent : undefined,
              only_errors: workflowOnlyErrors,
              limit: 200,
            });
            setWorkflow(w2);
          }
        } catch {
          // ignore
        }
      })();
    }, 1200);
    return () => clearInterval(t);
  }, [viewMode, selectedPlanId, createPlanJob?.status, createPlanJob?.plan_id, workflowScopes, workflowAgent, workflowOnlyErrors]);

  useEffect(() => {
    if (!createPlanJobId) return;
    localStorage.setItem("create_plan_job_id", createPlanJobId);
    const t = setInterval(() => {
      api
        .getJob(createPlanJobId)
        .then((j) => {
          setCreatePlanJob(j);
          // Auto switch to the plan when done and plan_id is known.
          if (autoSelectPlanFromJob && j.status !== "RUNNING" && j.plan_id) {
            setSelectedPlanId(j.plan_id);
            setAutoSelectPlanFromJob(false);
          }
        })
        .catch((e) => {
          const msg = String(e);
          log(msg);
          setCreatePlanJob(null);
          // If backend says job not found (state overwritten / cleared), stop polling and let user start again.
          if (msg.includes("404") || msg.toLowerCase().includes("job not found")) {
            setCreatePlanJobId(null);
            localStorage.removeItem("create_plan_job_id");
          }
        });
    }, 800);
    return () => clearInterval(t);
  }, [createPlanJobId, autoSelectPlanFromJob]);

  useEffect(() => {
    if (!selectedPlanId) return;
    api
      .getGraph(selectedPlanId)
      .then((g) => setGraph(g))
      .catch((e) => log(String(e)));
  }, [selectedPlanId]);

  const selectedNode: GraphNode | null = useMemo(() => {
    if (!graph || !selectedTaskId) return null;
    return graph.nodes.find((n) => n.task_id === selectedTaskId) ?? null;
  }, [graph, selectedTaskId]);

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
            <div className="title">{graph?.plan.title ?? "No Plan"}</div>
            <div className="muted mono">{graph?.plan.plan_id ?? ""}</div>
          </div>
          <div className="muted">
            status: <span className="mono">{graph?.running.task_id ? `Running(${graph.running.task_id.slice(0, 8)})` : "Pause"}</span>
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
                  const pid = selectedTopTaskHash ? undefined : selectedPlanId ?? undefined;
                  (async () => {
                    try {
                      if (selectedTopTaskHash) {
                        const w = await api.getWorkflow({
                          top_task_hash: selectedTopTaskHash,
                          scopes: workflowScopes.trim() ? workflowScopes : undefined,
                          agent: workflowAgent.trim() ? workflowAgent : undefined,
                          only_errors: workflowOnlyErrors,
                          limit: 200,
                        });
                        if ((w.returned_rows ?? w.nodes.length) > 0) {
                          setWorkflow(w);
                          return;
                        }
                      }
                      if (pid) {
                        const w2 = await api.getWorkflow({
                          plan_id: pid,
                          scopes: workflowScopes.trim() ? workflowScopes : undefined,
                          agent: workflowAgent.trim() ? workflowAgent : undefined,
                          only_errors: workflowOnlyErrors,
                          limit: 200,
                        });
                        setWorkflow(w2);
                      }
                    } catch (e) {
                      log(String(e));
                    }
                  })();
                }}
              >
                Refresh
              </button>
            </div>
            <div style={{ flex: 1, minHeight: 0 }}>
              {workflow ? <LLMWorkflowGraph workflow={workflow} onSelectCall={(id) => setSelectedLlmCallId(id)} /> : <div className="muted">loading workflow...</div>}
            </div>
          </div>
        ) : (
          <div className="panel graphWrap">
            {graph ? <TaskGraph nodes={graph.nodes} edges={graph.edges} onSelectNode={(id) => setSelectedTaskId(id)} /> : <div className="muted">no graph</div>}
          </div>
        )}
      </div>
      <div className="right">
        <ReviewSuggestionsPanel llmCallId={viewMode === "WORKFLOW" ? selectedLlmCallId : null} />
        {viewMode === "WORKFLOW" ? (
          <LLMCallDetails llmCallId={selectedLlmCallId} />
        ) : viewMode === "TASK" ? (
          <NodeDetails node={selectedNode} planId={selectedPlanId} onRefresh={() => refresh().catch((e) => log(String(e)))} />
        ) : null}
      </div>
    </div>
  );
}

import React, { useEffect, useMemo, useState } from "react";
import * as api from "./api";
import type { ConfigResp, CreatePlanJobResp, GraphNode, GraphV1, LlmCallsQueryResp, PlansResp } from "./types";
import ControlPanel from "./components/ControlPanel";
import TaskGraph from "./components/TaskGraph";
import NodeDetails from "./components/NodeDetails";
import CreatePlanProgress from "./components/CreatePlanProgress";
import LLMWorkflowGraph from "./components/LLMWorkflowGraph";
import LLMCallDetails from "./components/LLMCallDetails";
import type { WorkflowResp } from "./types";

export default function App() {
  const [config, setConfig] = useState<ConfigResp | null>(null);
  const [plans, setPlans] = useState<PlansResp["plans"]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [graph, setGraph] = useState<GraphV1 | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [topTask, setTopTask] = useState<string>("");
  const [logText, setLogText] = useState<string>("");
  const [createPlanJobId, setCreatePlanJobId] = useState<string | null>(() => localStorage.getItem("create_plan_job_id"));
  const [createPlanJob, setCreatePlanJob] = useState<CreatePlanJobResp | null>(null);
  const [createPlanTimeline, setCreatePlanTimeline] = useState<LlmCallsQueryResp["calls"]>([]);
  const [viewMode, setViewMode] = useState<"TASK" | "WORKFLOW">("TASK");
  const [workflow, setWorkflow] = useState<WorkflowResp | null>(null);
  const [selectedLlmCallId, setSelectedLlmCallId] = useState<string | null>(null);
  const [workflowScopes, setWorkflowScopes] = useState<string>("PLAN_GEN,PLAN_REVIEW");
  const [workflowAgent, setWorkflowAgent] = useState<string>("");
  const [workflowOnlyErrors, setWorkflowOnlyErrors] = useState<boolean>(false);

  function log(s: string) {
    setLogText((prev) => (prev ? prev + "\n\n" + s : s));
  }

  async function refresh() {
    const [cfg, pls] = await Promise.all([api.getConfig(), api.getPlans()]);
    setConfig(cfg);
    setPlans(pls.plans);
    const pid = selectedPlanId ?? (pls.plans.length ? pls.plans[0].plan_id : null);
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
      const pid = selectedPlanId ?? undefined;
      api
        .getWorkflow({
          plan_id: pid,
          scopes: workflowScopes.trim() ? workflowScopes : undefined,
          agent: workflowAgent.trim() ? workflowAgent : undefined,
          only_errors: workflowOnlyErrors,
          limit: 200,
        })
        .then((w) => setWorkflow(w))
        .catch(() => {});
    }, 1200);
    return () => clearInterval(t);
  }, [viewMode, selectedPlanId, workflowScopes, workflowAgent, workflowOnlyErrors]);

  useEffect(() => {
    if (!createPlanJobId) return;
    localStorage.setItem("create_plan_job_id", createPlanJobId);
    const t = setInterval(() => {
      api
        .getJob(createPlanJobId)
        .then((j) => {
          setCreatePlanJob(j);
          // Auto switch to the plan when done and plan_id is known.
          if (j.status !== "RUNNING" && j.plan_id) {
            setSelectedPlanId(j.plan_id);
          }
          const havePlanId = Boolean(j.plan_id);
          const params = havePlanId
            ? { plan_id: j.plan_id!, scopes: "PLAN_GEN,PLAN_REVIEW", limit: 200 }
            : { plan_id_missing: true, scopes: "PLAN_GEN", limit: 50 };
          api
            .getLlmCallsQuery(params)
            .then((resp) => setCreatePlanTimeline(resp.calls))
            .catch(() => {});
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
  }, [createPlanJobId]);

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
          onSelectPlanId={(v) => setSelectedPlanId(v)}
          onCreatePlanJobId={(jobId) => setCreatePlanJobId(jobId)}
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
            running: <span className="mono">{graph?.running.task_id ? graph.running.task_id.slice(0, 8) : "-"}</span>
          </div>
          <div className="spacer" />
          <div className="row" style={{ gap: 8 }}>
            <button className={viewMode === "TASK" ? "pillBtn active" : "pillBtn"} onClick={() => setViewMode("TASK")}>
              Task Graph
            </button>
            <button className={viewMode === "WORKFLOW" ? "pillBtn active" : "pillBtn"} onClick={() => setViewMode("WORKFLOW")}>
              LLM Workflow
            </button>
          </div>
        </div>
        {viewMode === "WORKFLOW" ? (
          <div className="panel" style={{ padding: 12, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div className="row" style={{ gap: 8, marginBottom: 10 }}>
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
              <div className="spacer" />
              <button
                onClick={() => {
                  const pid = selectedPlanId ?? undefined;
                  api
                    .getWorkflow({
                      plan_id: pid,
                      scopes: workflowScopes.trim() ? workflowScopes : undefined,
                      agent: workflowAgent.trim() ? workflowAgent : undefined,
                      only_errors: workflowOnlyErrors,
                      limit: 200,
                    })
                    .then((w) => setWorkflow(w))
                    .catch((e) => log(String(e)));
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
        <CreatePlanProgress
          job={createPlanJob}
          timeline={createPlanTimeline}
          onSelectPlanId={(pid) => setSelectedPlanId(pid)}
        />
        {viewMode === "WORKFLOW" ? <LLMCallDetails llmCallId={selectedLlmCallId} /> : <NodeDetails node={selectedNode} />}
        <div className="panel">
          <h3>Logs</h3>
          <textarea className="log" value={logText} readOnly rows={10} />
        </div>
      </div>
    </div>
  );
}

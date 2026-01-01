import React, { useEffect, useMemo, useState } from "react";
import * as api from "./api";
import type { ConfigResp, GraphNode, GraphV1, PlansResp } from "./types";
import ControlPanel from "./components/ControlPanel";
import TaskGraph from "./components/TaskGraph";
import NodeDetails from "./components/NodeDetails";

export default function App() {
  const [config, setConfig] = useState<ConfigResp | null>(null);
  const [plans, setPlans] = useState<PlansResp["plans"]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [graph, setGraph] = useState<GraphV1 | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [topTask, setTopTask] = useState<string>("");
  const [logText, setLogText] = useState<string>("");

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
        </div>
        <div className="panel graphWrap">{graph ? <TaskGraph nodes={graph.nodes} edges={graph.edges} onSelectNode={(id) => setSelectedTaskId(id)} /> : <div className="muted">no graph</div>}</div>
      </div>
      <div className="right">
        <NodeDetails node={selectedNode} />
        <div className="panel">
          <h3>Debug (placeholder)</h3>
          <div className="muted">LLM Explorer will be added later.</div>
        </div>
        <div className="panel">
          <h3>Logs</h3>
          <textarea className="log" value={logText} readOnly rows={10} />
        </div>
      </div>
    </div>
  );
}


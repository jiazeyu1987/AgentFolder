import React, { useMemo, useState } from "react";
import type { ConfigResp, PlansResp } from "../types";
import * as api from "../api";

export default function ControlPanel(props: {
  config: ConfigResp | null;
  plans: PlansResp["plans"];
  selectedPlanId: string | null;
  onSelectPlanId: (v: string) => void;
  topTask: string;
  onTopTaskChange: (v: string) => void;
  onRefresh: () => void;
  onLog: (s: string) => void;
}) {
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [maxIterations, setMaxIterations] = useState(10000);
  const [includeReviews, setIncludeReviews] = useState(false);

  const planOptions = useMemo(() => props.plans, [props.plans]);

  async function onCopy(text: string) {
    await navigator.clipboard.writeText(text);
    props.onLog("copied to clipboard");
  }

  return (
    <div className="panel">
      <h3>Control</h3>

      <div className="field">
        <label>Plan</label>
        <select
          value={props.selectedPlanId ?? ""}
          onChange={(e) => props.onSelectPlanId(e.target.value)}
          disabled={planOptions.length === 0}
        >
          {planOptions.length === 0 ? <option value="">(no plans)</option> : null}
          {planOptions.map((p) => (
            <option key={p.plan_id} value={p.plan_id}>
              {p.title} ({p.plan_id.slice(0, 8)})
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>TopTask</label>
        <textarea value={props.topTask} onChange={(e) => props.onTopTaskChange(e.target.value)} rows={4} />
        <div className="row">
          <button onClick={() => onCopy(props.topTask)}>TopTask</button>
          <div className="spacer" />
          <label className="inline">
            max-attempts
            <input type="number" value={maxAttempts} min={1} max={50} onChange={(e) => setMaxAttempts(Number(e.target.value))} />
          </label>
        </div>
      </div>

      <div className="row">
        <button
          onClick={async () => {
            props.onLog("create-plan...");
            const res = await api.createPlan(props.topTask, maxAttempts);
            props.onLog(JSON.stringify(res, null, 2));
            props.onRefresh();
          }}
          disabled={!props.topTask.trim()}
        >
          Create Plan
        </button>
      </div>

      <div className="row">
        <button
          onClick={async () => {
            props.onLog("run start...");
            const res = await api.runStart(maxIterations);
            props.onLog(JSON.stringify(res, null, 2));
          }}
        >
          Run
        </button>
        <button
          onClick={async () => {
            props.onLog("status refresh...");
            props.onRefresh();
          }}
        >
          Status
        </button>
      </div>

      <div className="row">
        <button
          className="danger"
          onClick={async () => {
            const ok1 = confirm("Delete ALL DB data? (This removes state/state.db)");
            if (!ok1) return;
            const ok2 = confirm("Also purge workspace/*, tasks/*, and logs/*?");
            const res = await api.resetDb(ok2);
            props.onLog(JSON.stringify(res, null, 2));
            props.onRefresh();
          }}
        >
          Reset DB
        </button>
        <button
          onClick={async () => {
            if (!props.selectedPlanId) return;
            props.onLog("export...");
            const res = await api.exportDeliverables(props.selectedPlanId, includeReviews);
            props.onLog(JSON.stringify(res, null, 2));
          }}
          disabled={!props.selectedPlanId}
        >
          Export
        </button>
      </div>

      <div className="field">
        <label className="inline">
          include reviews
          <input type="checkbox" checked={includeReviews} onChange={(e) => setIncludeReviews(e.target.checked)} />
        </label>
      </div>

      <div className="field">
        <label className="inline">
          max-iterations
          <input type="number" value={maxIterations} min={1} onChange={(e) => setMaxIterations(Number(e.target.value))} />
        </label>
      </div>

      <h4>Paths</h4>
      {props.config ? (
        <div className="paths">
          {Object.entries(props.config.paths).map(([k, v]) => (
            <div key={k} className="pathRow">
              <div className="pathKey">{k}</div>
              <div className="pathVal" title={v}>
                {v}
              </div>
              <button onClick={() => onCopy(v)}>Copy</button>
            </div>
          ))}
        </div>
      ) : (
        <div className="muted">loading...</div>
      )}
    </div>
  );
}


import React, { useMemo, useState } from "react";
import type { ConfigResp, PlansResp } from "../types";
import * as api from "../api";

function getNumber(obj: unknown, key: string): number | null {
  if (!obj || typeof obj !== "object") return null;
  const v = (obj as any)[key];
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() && Number.isFinite(Number(v))) return Number(v);
  return null;
}

export default function ControlPanel(props: {
  config: ConfigResp | null;
  plans: PlansResp["plans"];
  selectedPlanId: string | null;
  onSelectPlanId: (v: string) => void;
  onCreatePlanJobId: (jobId: string) => void;
  onOpenErrorAnalysis: () => void;
  onOpenAuditLog: () => void;
  topTask: string;
  onTopTaskChange: (v: string) => void;
  onRefresh: () => void;
  onLog: (s: string) => void;
}) {
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [maxIterations, setMaxIterations] = useState(10000);
  const [includeReviews, setIncludeReviews] = useState(false);
  const [keepTrying, setKeepTrying] = useState(false);
  const [maxTotalAttempts, setMaxTotalAttempts] = useState<number | "">("");
  const [maxDepth, setMaxDepth] = useState<number>(5);
  const [oneShotDays, setOneShotDays] = useState<number>(10);
  const [planPassScore, setPlanPassScore] = useState<number>(90);
  const [createPlanPending, setCreatePlanPending] = useState(false);
  const [createPlanCooldown, setCreatePlanCooldown] = useState(false);
  const [createPlanAck, setCreatePlanAck] = useState<string | null>(null);
  const [runPending, setRunPending] = useState(false);
  const [runCooldown, setRunCooldown] = useState(false);
  const [runAck, setRunAck] = useState<string | null>(null);

  const planOptions = useMemo(() => props.plans, [props.plans]);
  const planTitleCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of props.plans) m.set(p.title, (m.get(p.title) ?? 0) + 1);
    return m;
  }, [props.plans]);
  const planTitleVersion = useMemo(() => {
    // Version numbering is per-title and based on creation time:
    // earliest is v1 (hidden), second is v2, third is v3, ...
    const byTitle = new Map<string, Array<{ plan_id: string; created_at: string }>>();
    for (const p of props.plans) {
      const arr = byTitle.get(p.title) ?? [];
      arr.push({ plan_id: p.plan_id, created_at: p.created_at });
      byTitle.set(p.title, arr);
    }
    const version = new Map<string, number>();
    for (const [title, arr] of byTitle.entries()) {
      if (arr.length <= 1) continue;
      arr.sort((a, b) => a.created_at.localeCompare(b.created_at)); // asc
      for (let i = 0; i < arr.length; i++) {
        version.set(arr[i].plan_id, i + 1);
      }
    }
    return version;
  }, [props.plans]);

  async function onCopy(text: string) {
    await navigator.clipboard.writeText(text);
    props.onLog("copied to clipboard");
  }

  function startCooldown(setter: (v: boolean) => void, ackSetter: (v: string | null) => void) {
    setter(true);
    setTimeout(() => {
      setter(false);
      ackSetter(null);
    }, 1000);
  }

  // Initialize config fields once config arrives.
  const cfgRaw = props.config?.runtime_config;
  React.useEffect(() => {
    if (!cfgRaw) return;
    const md = getNumber(cfgRaw, "max_decomposition_depth");
    const os = getNumber(cfgRaw, "one_shot_threshold_person_days");
    const ps = getNumber(cfgRaw, "plan_review_pass_score");
    if (md !== null) setMaxDepth(md);
    if (os !== null) setOneShotDays(os);
    if (ps !== null) setPlanPassScore(ps);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.config?.runtime_config]);

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
              {planTitleCounts.get(p.title) && (planTitleCounts.get(p.title) ?? 0) > 1
                ? planTitleVersion.get(p.plan_id) && (planTitleVersion.get(p.plan_id) ?? 1) > 1
                  ? `(v${planTitleVersion.get(p.plan_id)}) `
                  : ""
                : ""}
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
          className="success"
          onClick={async () => {
            if (createPlanPending || createPlanCooldown) return;
            setCreatePlanPending(true);
            setCreatePlanAck("sending…");
            try {
              const res = await api.createPlanAsync(props.topTask, maxAttempts, keepTrying, maxTotalAttempts === "" ? undefined : maxTotalAttempts);
              if (res.job_id) props.onCreatePlanJobId(res.job_id);
              setCreatePlanAck(res.started ? "started" : "already running");
              props.onRefresh();
            } catch (e) {
              setCreatePlanAck("failed");
              props.onLog(String(e));
            } finally {
              setCreatePlanPending(false);
              startCooldown(setCreatePlanCooldown, setCreatePlanAck);
            }
          }}
          disabled={createPlanPending || createPlanCooldown}
        >
          {createPlanPending ? "Create Plan…" : "Create Plan"}
        </button>
        <button
          onClick={() => {
            props.onOpenErrorAnalysis();
          }}
        >
          错误分析
        </button>
        <button
          onClick={() => {
            props.onOpenAuditLog();
          }}
        >
          动作日志
        </button>
      </div>
      {createPlanAck ? <div className="muted">Create Plan: {createPlanAck}</div> : null}

      <div className="field">
        <label className="inline">
          keep-trying
          <input type="checkbox" checked={keepTrying} onChange={(e) => setKeepTrying(e.target.checked)} />
        </label>
      </div>
      <div className="field">
        <label className="inline">
          max-total-attempts
          <input
            type="number"
            value={maxTotalAttempts}
            min={1}
            onChange={(e) => setMaxTotalAttempts(e.target.value === "" ? "" : Number(e.target.value))}
            disabled={!keepTrying}
          />
        </label>
      </div>

      <div className="row">
        <button
          className="success"
          onClick={async () => {
            if (runPending || runCooldown) return;
            setRunPending(true);
            setRunAck("sending…");
            try {
              await api.runStart(maxIterations);
              setRunAck("started");
            } catch (e) {
              setRunAck("failed");
              props.onLog(String(e));
            } finally {
              setRunPending(false);
              startCooldown(setRunCooldown, setRunAck);
            }
          }}
          disabled={runPending || runCooldown}
        >
          {runPending ? "Run…" : "Run"}
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
      {runAck ? <div className="muted">Run: {runAck}</div> : null}

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

      <h4>Runtime Config</h4>
      <div className="field">
        <label className="inline">
          最大深度 max_decomposition_depth
          <input type="number" value={maxDepth} min={1} max={50} onChange={(e) => setMaxDepth(Number(e.target.value))} />
        </label>
      </div>
      <div className="field">
        <label className="inline">
          最小LLM天数 one_shot_threshold_person_days
          <input type="number" value={oneShotDays} min={0.1} step={0.5} onChange={(e) => setOneShotDays(Number(e.target.value))} />
        </label>
      </div>
      <div className="field">
        <label className="inline">
          plan_review_pass_score (pass if score ≥ this)
          <input type="number" value={planPassScore} min={1} max={100} onChange={(e) => setPlanPassScore(Number(e.target.value))} />
        </label>
      </div>
      <div className="row">
        <button
          onClick={async () => {
            props.onLog("save runtime_config...");
            const res = await api.updateRuntimeConfig({
              max_decomposition_depth: maxDepth,
              one_shot_threshold_person_days: oneShotDays,
              plan_review_pass_score: planPassScore,
            });
            props.onLog(JSON.stringify(res, null, 2));
            props.onRefresh();
          }}
        >
          Save Config
        </button>
        <div className="spacer" />
        <button
          onClick={() => {
            const payload = { max_decomposition_depth: maxDepth, one_shot_threshold_person_days: oneShotDays, plan_review_pass_score: planPassScore };
            onCopy(JSON.stringify(payload));
          }}
        >
          Copy
        </button>
      </div>
    </div>
  );
}

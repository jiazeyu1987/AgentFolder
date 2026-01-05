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
  const [showSettings, setShowSettings] = useState(false);

  const [maxIterations, setMaxIterations] = useState(10000);
  const [includeReviews, setIncludeReviews] = useState(false);
  const [keepTrying, setKeepTrying] = useState(false);
  const [maxTotalAttempts, setMaxTotalAttempts] = useState<number | "">("");

  const [maxDepth, setMaxDepth] = useState<number>(5);
  const [oneShotDays, setOneShotDays] = useState<number>(10);
  const [createPlanMaxAttempts, setCreatePlanMaxAttempts] = useState<number>(3);
  const [taskMaxAttempts, setTaskMaxAttempts] = useState<number>(13);
  const [planPassScore, setPlanPassScore] = useState<number>(90);
  const [planReviewNotesMaxChars, setPlanReviewNotesMaxChars] = useState<number>(500);
  const [taskReviewPassScore, setTaskReviewPassScore] = useState<number>(90);
  const [taskReviewNotesMaxChars, setTaskReviewNotesMaxChars] = useState<number>(500);

  const [createPlanPending, setCreatePlanPending] = useState(false);
  const [createPlanCooldown, setCreatePlanCooldown] = useState(false);
  const [createPlanAck, setCreatePlanAck] = useState<string | null>(null);

  const [runPending, setRunPending] = useState(false);
  const [runCooldown, setRunCooldown] = useState(false);
  const [runAck, setRunAck] = useState<string | null>(null);

  const [resetToPlanAck, setResetToPlanAck] = useState<string | null>(null);

  const planOptions = useMemo(() => props.plans, [props.plans]);

  async function onCopy(text: string) {
    await navigator.clipboard.writeText(text);
    props.onLog("已复制到剪贴板");
  }

  function startCooldown(setter: (v: boolean) => void, ackSetter: (v: string | null) => void) {
    setter(true);
    setTimeout(() => {
      setter(false);
      ackSetter(null);
    }, 1000);
  }

  const pathLabel: Record<string, string> = {
    INPUTS_DIR: "输入目录",
    BASELINE_INPUTS_DIR: "基线输入目录",
    DB_PATH_DEFAULT: "数据库文件",
    DELIVERABLES_DIR: "统一交付物目录",
    REQUIRED_DOCS_DIR: "缺输入清单目录",
    REVIEWS_DIR: "评审目录",
    ARTIFACTS_DIR: "产物目录（旧）",
    LOGS_DIR: "日志目录",
  };

  const cfgRaw = props.config?.runtime_config;
  React.useEffect(() => {
    if (!cfgRaw) return;
    const md = getNumber(cfgRaw, "max_decomposition_depth");
    const os = getNumber(cfgRaw, "one_shot_threshold_person_days");
    const ca = getNumber(cfgRaw, "create_plan_max_attempts");
    const ta = getNumber(cfgRaw, "task_max_attempts");
    const trps = getNumber(cfgRaw, "task_review_pass_score");
    const trn = getNumber(cfgRaw, "task_review_notes_max_chars");
    const ps = getNumber(cfgRaw, "plan_review_pass_score");
    const rn = getNumber(cfgRaw, "plan_review_notes_max_chars");
    if (md !== null) setMaxDepth(md);
    if (os !== null) setOneShotDays(os);
    if (ca !== null) setCreatePlanMaxAttempts(ca);
    if (ta !== null) setTaskMaxAttempts(ta);
    if (trps !== null) setTaskReviewPassScore(trps);
    if (trn !== null) setTaskReviewNotesMaxChars(trn);
    if (ps !== null) setPlanPassScore(ps);
    if (rn !== null) setPlanReviewNotesMaxChars(rn);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.config?.runtime_config]);

  return (
    <div className="panel">
      <h3>控制面板</h3>

      <div className="field">
        <label>计划</label>
        <select value={props.selectedPlanId ?? ""} onChange={(e) => props.onSelectPlanId(e.target.value)} disabled={planOptions.length === 0}>
          {planOptions.length === 0 ? <option value="">（暂无计划）</option> : null}
          {planOptions.map((p) => (
            <option key={p.plan_id} value={p.plan_id}>
              {p.title} ({p.plan_id.slice(0, 8)})
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>顶层任务（TopTask）</label>
        <textarea value={props.topTask} onChange={(e) => props.onTopTaskChange(e.target.value)} rows={4} />
        <div className="row">
          <button onClick={() => onCopy(props.topTask)}>复制 TopTask</button>
          <div className="spacer" />
        </div>
      </div>

      <div className="btnGrid">
        <button
          className="success btnLarge"
          onClick={async () => {
            if (createPlanPending || createPlanCooldown) return;
            setCreatePlanPending(true);
            setCreatePlanAck("发送中…");
            try {
              const res = await api.createPlanAsync(props.topTask, {
                keep_trying: keepTrying,
                max_total_attempts: maxTotalAttempts === "" ? undefined : maxTotalAttempts,
              });
              if (res.job_id) props.onCreatePlanJobId(res.job_id);
              setCreatePlanAck(res.started ? "已启动" : "已有任务在运行");
              props.onRefresh();
            } catch (e) {
              setCreatePlanAck("失败");
              props.onLog(String(e));
            } finally {
              setCreatePlanPending(false);
              startCooldown(setCreatePlanCooldown, setCreatePlanAck);
            }
          }}
          disabled={createPlanPending || createPlanCooldown}
          title="生成/评审计划（create-plan）"
        >
          {createPlanPending ? "创建计划…" : "创建计划"}
        </button>

        <button
          className="success btnLarge"
          onClick={async () => {
            if (runPending || runCooldown) return;
            setRunPending(true);
            setRunAck("发送中…");
            try {
              await api.runStart(maxIterations);
              setRunAck("已启动");
            } catch (e) {
              setRunAck("失败");
              props.onLog(String(e));
            } finally {
              setRunPending(false);
              startCooldown(setRunCooldown, setRunAck);
            }
          }}
          disabled={runPending || runCooldown}
          title="启动 run 后台循环"
        >
          {runPending ? "运行…" : "运行"}
        </button>

        <button
          className="btnLarge"
          onClick={async () => {
            props.onLog("刷新状态…");
            props.onRefresh();
          }}
          title="手动刷新页面数据"
        >
          刷新
        </button>

        <button className="btnLarge" onClick={() => setShowSettings(true)} title="打开设置面板">
          设置
        </button>

        <button
          className="btnLarge"
          onClick={async () => {
            if (!props.selectedPlanId) return;
            props.onLog("导出…");
            const res = await api.exportDeliverables(props.selectedPlanId, includeReviews);
            props.onLog(JSON.stringify(res, null, 2));
          }}
          disabled={!props.selectedPlanId}
          title="导出交付物（export）"
        >
          导出交付物
        </button>

        <button
          className="danger btnLarge"
          onClick={async () => {
            if (!props.selectedPlanId) return;
            const ok = confirm("仅删除“执行阶段”结果，恢复到 create-plan 之后、run 之前的状态？");
            if (!ok) return;
            setResetToPlanAck("发送中…");
            try {
              const res = await api.resetToPlan(props.selectedPlanId);
              setResetToPlanAck(res.exit_code === 0 ? "完成" : "失败");
              props.onLog(res.stdout || res.stderr || "");
              props.onRefresh();
            } catch (e) {
              setResetToPlanAck("失败");
              props.onLog(String(e));
            } finally {
              setTimeout(() => setResetToPlanAck(null), 1000);
            }
          }}
          disabled={!props.selectedPlanId}
          title="仅清空 run 产生的状态/产物（保留 plan）"
        >
          重置执行
        </button>

        <button
          className="danger btnLarge"
          onClick={async () => {
            const ok1 = confirm("删除所有 DB 数据？（会删除 state/state.db）");
            if (!ok1) return;
            const ok2 = confirm("同时清空 workspace/*、tasks/*、logs/* 吗？");
            const res = await api.resetDb(ok2);
            props.onLog(JSON.stringify(res, null, 2));
            props.onRefresh();
          }}
          title="删除 DB（可选清空工作区）"
        >
          重置 DB
        </button>
      </div>
      {createPlanAck ? <div className="muted">创建计划：{createPlanAck}</div> : null}

      {runAck ? <div className="muted">运行：{runAck}</div> : null}
      {resetToPlanAck ? <div className="muted">重置执行：{resetToPlanAck}</div> : null}

      {showSettings ? (
        <div className="modalOverlay" onClick={() => setShowSettings(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="row" style={{ marginBottom: 10 }}>
              <div className="title">设置</div>
              <div className="spacer" />
              <button onClick={() => setShowSettings(false)}>关闭</button>
            </div>

            <h4>运行参数</h4>
            <div className="field">
              <label className="inline">
                最大迭代次数（max-iterations）
                <input type="number" value={maxIterations} min={1} onChange={(e) => setMaxIterations(Number(e.target.value))} />
              </label>
            </div>

            <h4>Create Plan 参数</h4>
            <div className="field">
              <label className="inline">
                自动继续尝试（keep-trying）
                <input type="checkbox" checked={keepTrying} onChange={(e) => setKeepTrying(e.target.checked)} />
              </label>
            </div>
            <div className="field">
              <label className="inline">
                总尝试上限（max-total-attempts）
                <input
                  type="number"
                  value={maxTotalAttempts}
                  min={1}
                  onChange={(e) => setMaxTotalAttempts(e.target.value === "" ? "" : Number(e.target.value))}
                  disabled={!keepTrying}
                />
              </label>
            </div>

            <h4>导出参数</h4>
            <div className="field">
              <label className="inline">
                导出包含评审文件（include-reviews）
                <input type="checkbox" checked={includeReviews} onChange={(e) => setIncludeReviews(e.target.checked)} />
              </label>
            </div>

            <h4>路径</h4>
            {props.config ? (
              <div className="paths">
                {Object.entries(props.config.paths).map(([k, v]) => (
                  <div key={k} className="pathRow">
                    <div className="pathKey" title={k}>
                      {pathLabel[k] ? `${pathLabel[k]}（${k}）` : k}
                    </div>
                    <div className="pathVal" title={v}>
                      {v}
                    </div>
                    <button onClick={() => onCopy(v)}>复制</button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted">加载中…</div>
            )}

            <h4>运行配置（runtime_config.json）</h4>
            <div className="field">
              <label className="inline">
                最大深度（max_decomposition_depth）
                <input type="number" value={maxDepth} min={1} max={50} onChange={(e) => setMaxDepth(Number(e.target.value))} />
              </label>
            </div>
            <div className="field">
              <label className="inline">
                一次 LLM 可完成阈值（人/日）（one_shot_threshold_person_days）
                <input type="number" value={oneShotDays} min={0.1} step={0.5} onChange={(e) => setOneShotDays(Number(e.target.value))} />
              </label>
            </div>
            <div className="field">
              <label className="inline">
                Create Plan 最大尝试次数（create_plan_max_attempts）
                <input type="number" value={createPlanMaxAttempts} min={1} max={100} onChange={(e) => setCreatePlanMaxAttempts(Number(e.target.value))} />
              </label>
            </div>
            <div className="field">
              <label className="inline">
                单个任务最大尝试次数（task_max_attempts）
                <input type="number" value={taskMaxAttempts} min={1} max={200} onChange={(e) => setTaskMaxAttempts(Number(e.target.value))} />
              </label>
            </div>
            <div className="field">
              <label className="inline">
                Plan 通过分数（plan_review_pass_score）
                <input type="number" value={planPassScore} min={1} max={100} onChange={(e) => setPlanPassScore(Number(e.target.value))} />
              </label>
            </div>
            <div className="field">
              <label className="inline">
                Review Notes 字数上限（plan_review_notes_max_chars）
                <input
                  type="number"
                  value={planReviewNotesMaxChars}
                  min={50}
                  max={20000}
                  step={50}
                  onChange={(e) => setPlanReviewNotesMaxChars(Number(e.target.value))}
                />
              </label>
            </div>
            <div className="field">
              <label className="inline">
                Task 评审通过分数（task_review_pass_score）
                <input type="number" value={taskReviewPassScore} min={1} max={100} onChange={(e) => setTaskReviewPassScore(Number(e.target.value))} />
              </label>
            </div>
            <div className="field">
              <label className="inline">
                Task 整改单字数上限（task_review_notes_max_chars）
                <input
                  type="number"
                  value={taskReviewNotesMaxChars}
                  min={50}
                  max={20000}
                  step={50}
                  onChange={(e) => setTaskReviewNotesMaxChars(Number(e.target.value))}
                />
              </label>
            </div>

            <div className="row">
              <button
                onClick={async () => {
                  props.onLog("保存 runtime_config…");
                  const res = await api.updateRuntimeConfig({
                    max_decomposition_depth: maxDepth,
                    one_shot_threshold_person_days: oneShotDays,
                    create_plan_max_attempts: createPlanMaxAttempts,
                    task_max_attempts: taskMaxAttempts,
                    plan_review_pass_score: planPassScore,
                    plan_review_notes_max_chars: planReviewNotesMaxChars,
                    task_review_pass_score: taskReviewPassScore,
                    task_review_notes_max_chars: taskReviewNotesMaxChars,
                  });
                  props.onLog(JSON.stringify(res, null, 2));
                  props.onRefresh();
                }}
              >
                保存配置
              </button>
              <div className="spacer" />
              <button
                onClick={() => {
                  const payload = {
                    max_decomposition_depth: maxDepth,
                    one_shot_threshold_person_days: oneShotDays,
                    create_plan_max_attempts: createPlanMaxAttempts,
                    task_max_attempts: taskMaxAttempts,
                    plan_review_pass_score: planPassScore,
                    plan_review_notes_max_chars: planReviewNotesMaxChars,
                    task_review_pass_score: taskReviewPassScore,
                    task_review_notes_max_chars: taskReviewNotesMaxChars,
                  };
                  onCopy(JSON.stringify(payload));
                }}
              >
                复制配置 JSON
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

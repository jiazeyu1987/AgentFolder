import { useMemo } from "react";
import * as api from "../api";
import type { WorkflowResp } from "../types";
import { useInterval } from "./useInterval";
import { useRequestState } from "./useRequestState";

export function useWorkflowData(params: {
  enabled: boolean;
  pollMs?: number;
  createPlanJobId?: string | null;
  createPlanRunning?: boolean;
  selectedPlanId?: string | null;
  selectedTopTaskHash?: string | null;
  scopes?: string;
  agent?: string;
  onlyErrors?: boolean;
}) {
  const enabled = Boolean(params.enabled);
  const pollMs = params.pollMs ?? 1200;

  const { state, refresh } = useRequestState<WorkflowResp>({
    enabled,
    key: [
      params.createPlanJobId || "",
      params.createPlanRunning ? "1" : "0",
      params.selectedPlanId || "",
      params.selectedTopTaskHash || "",
      params.scopes || "",
      params.agent || "",
      params.onlyErrors ? "1" : "0",
    ].join("|"),
    load: async () => {
      const followJob = Boolean(params.createPlanRunning) && Boolean(params.createPlanJobId);
      const scopes = params.scopes?.trim() ? params.scopes : undefined;
      const agent = params.agent?.trim() ? params.agent : undefined;
      const onlyErrors = Boolean(params.onlyErrors);

      if (followJob && params.createPlanJobId) {
        const w = await api.getWorkflow({
          job_id: params.createPlanJobId,
          plan_id_missing: false,
          scopes,
          agent,
          only_errors: onlyErrors,
          limit: 200,
        });
        // If create-plan job is running but hasn't emitted workflow rows yet, keep it as a valid empty payload.
        if ((w.returned_rows ?? w.nodes.length) > 0) return w;
        // Fall through to plan/top_task if we can.
      }

      if (params.selectedTopTaskHash) {
        const w = await api.getWorkflow({
          top_task_hash: params.selectedTopTaskHash,
          plan_id_missing: false,
          scopes,
          agent,
          only_errors: onlyErrors,
          limit: 200,
        });
        if ((w.returned_rows ?? w.nodes.length) > 0) return w;
      }

      if (params.selectedPlanId) {
        return api.getWorkflow({
          plan_id: params.selectedPlanId,
          plan_id_missing: false,
          scopes,
          agent,
          only_errors: onlyErrors,
          limit: 200,
        });
      }

      // no plan selected: return empty workflow so UI doesn't hang
      return {
        schema_version: "workflow_v1",
        plan: { plan_id: null, title: null, workflow_mode: "UNKNOWN" },
        nodes: [],
        edges: [],
        groups: [],
        returned_rows: 0,
        ts: new Date().toISOString(),
      };
    },
    initialData: null,
  });

  useInterval(
    () => {
      refresh();
    },
    enabled ? pollMs : null
  );

  return useMemo(() => ({ ...state, refresh }), [state, refresh]);
}

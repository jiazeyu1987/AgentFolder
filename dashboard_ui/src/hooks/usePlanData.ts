import { useMemo } from "react";
import * as api from "../api";
import type { GraphV1, PlanSnapshotResp } from "../types";
import { useInterval } from "./useInterval";
import { useRequestState } from "./useRequestState";

export type PlanData = { graph: GraphV1; snapshot: PlanSnapshotResp };

export function usePlanData(planId: string | null, opts?: { pollMs?: number | null; enabled?: boolean }) {
  const enabled = Boolean(opts?.enabled ?? true) && Boolean(planId);
  const pollMs = opts?.pollMs ?? 2000;

  const { state, refresh } = useRequestState<PlanData>({
    enabled,
    key: planId,
    load: async () => {
      const pid = String(planId || "");
      const [graph, snapshot] = await Promise.all([api.getGraph(pid), api.getPlanSnapshot(pid)]);
      return { graph, snapshot };
    },
  });

  useInterval(
    () => {
      refresh();
    },
    enabled && pollMs !== null ? pollMs : null
  );

  return useMemo(() => ({ ...state, refresh }), [state, refresh]);
}

import { useMemo } from "react";
import * as api from "../api";
import type { RunStatusResp } from "../types";
import { useInterval } from "./useInterval";
import { useRequestState } from "./useRequestState";

export function useRunStatus(opts?: { pollMs?: number; enabled?: boolean; planId?: string | null }) {
  const enabled = opts?.enabled ?? true;
  const pollMs = opts?.pollMs ?? 1500;
  const planId = opts?.planId ?? null;

  const { state, refresh } = useRequestState<RunStatusResp>({
    enabled,
    load: () => api.getRunStatusForPlan(planId),
    key: planId,
  });

  useInterval(
    () => {
      refresh();
    },
    enabled ? pollMs : null
  );

  return useMemo(() => ({ ...state, refresh }), [state, refresh]);
}

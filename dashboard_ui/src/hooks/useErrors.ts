import { useMemo } from "react";
import * as api from "../api";
import type { ErrorsResp } from "../types";
import { useInterval } from "./useInterval";
import { useRequestState } from "./useRequestState";

export function useErrors(params: {
  enabled: boolean;
  pollMs?: number;
  planId?: string | null;
  planIdMissing?: boolean;
  taskId?: string | null;
  includeRelated?: boolean;
  limit?: number;
}) {
  const enabled = Boolean(params.enabled) && (Boolean(params.planId) || Boolean(params.planIdMissing));
  const pollMs = params.pollMs ?? 1500;

  const queryKey = useMemo(
    () => [
      params.planId || "",
      params.planIdMissing ? "1" : "0",
      params.taskId || "",
      params.includeRelated ? "1" : "0",
      String(params.limit ?? 200),
    ].join("|"),
    [params.planId, params.planIdMissing, params.taskId, params.includeRelated, params.limit]
  );

  const { state, refresh } = useRequestState<ErrorsResp>({
    enabled,
    key: queryKey,
    load: () =>
      api.getErrors({
        plan_id: params.planId ?? undefined,
        plan_id_missing: params.planIdMissing ? true : undefined,
        task_id: params.taskId ?? undefined,
        include_related: params.includeRelated ? true : undefined,
        limit: params.limit ?? 200,
      }),
  });

  useInterval(
    () => {
      refresh();
    },
    enabled ? pollMs : null
  );

  return useMemo(() => ({ ...state, refresh }), [state, refresh]);
}


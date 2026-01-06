import { useMemo } from "react";
import * as api from "../api";
import type { AuditResp } from "../types";
import { useInterval } from "./useInterval";
import { useRequestState } from "./useRequestState";

export function useAudit(params: {
  enabled: boolean;
  pollMs?: number;
  topTaskHash?: string;
  planId?: string | null;
  category?: string;
  limit?: number;
}) {
  const enabled = Boolean(params.enabled) && Boolean(params.topTaskHash);
  const pollMs = params.pollMs ?? 1500;
  const key = useMemo(
    () => [params.topTaskHash || "", params.planId || "", params.category || "", String(params.limit ?? 300)].join("|"),
    [params.topTaskHash, params.planId, params.category, params.limit]
  );

  const { state, refresh } = useRequestState<AuditResp>({
    enabled,
    key,
    load: () =>
      api.getAudit({
        top_task_hash: params.topTaskHash,
        plan_id: params.planId ?? undefined,
        category: params.category?.trim() ? params.category.trim() : undefined,
        limit: params.limit ?? 300,
      }),
    ignoreError: (msg) => msg.includes("503") && msg.toLowerCase().includes("db reset in progress"),
  });

  useInterval(
    () => {
      refresh();
    },
    enabled ? pollMs : null
  );

  return useMemo(() => ({ ...state, refresh }), [state, refresh]);
}

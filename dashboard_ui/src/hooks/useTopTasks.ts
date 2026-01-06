import { useMemo } from "react";
import * as api from "../api";
import type { TopTasksResp } from "../types";
import { useRequestState } from "./useRequestState";

export function useTopTasks(limit = 50) {
  const { state, refresh } = useRequestState<TopTasksResp>({
    enabled: true,
    key: String(limit),
    load: () => api.getTopTasks(limit),
  });
  return useMemo(() => ({ ...state, refresh }), [state, refresh]);
}


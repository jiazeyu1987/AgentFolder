import { useMemo } from "react";
import * as api from "../api";
import type { PromptFileResp } from "../types";
import { useRequestState } from "./useRequestState";

export function useJobLog(params: { jobId: string | null; enabled: boolean; maxChars?: number; key?: unknown }) {
  const enabled = Boolean(params.enabled) && Boolean(params.jobId);
  const k = params.key ?? params.jobId ?? "";
  const maxChars = params.maxChars ?? 200_000;

  const { state, refresh } = useRequestState<PromptFileResp>({
    enabled,
    key: k,
    load: () => api.getJobLog(String(params.jobId), maxChars),
  });

  return useMemo(() => ({ ...state, refresh }), [state, refresh]);
}


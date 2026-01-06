import { useMemo } from "react";
import * as api from "../api";
import type { CreatePlanJobResp } from "../types";
import { useInterval } from "./useInterval";
import { useRequestState } from "./useRequestState";

export function useCreatePlanJob(params: {
  jobId: string | null;
  enabled?: boolean;
  pollMs?: number;
  onJobNotFound?: () => void;
}) {
  const enabled = Boolean(params.enabled ?? true) && Boolean(params.jobId);
  const pollMs = params.pollMs ?? 800;
  const jobId = params.jobId;

  const { state, refresh, setData } = useRequestState<CreatePlanJobResp>({
    enabled,
    key: jobId,
    load: async () => {
      return api.getJob(String(jobId));
    },
    onError: (msg) => {
      if (!jobId) return;
      if (msg.includes("404") || msg.toLowerCase().includes("job not found")) {
        try {
          params.onJobNotFound?.();
        } finally {
          setData(null);
        }
      }
    },
  });

  useInterval(
    () => {
      refresh();
    },
    enabled ? pollMs : null
  );

  return useMemo(() => ({ ...state, refresh }), [state, refresh]);
}


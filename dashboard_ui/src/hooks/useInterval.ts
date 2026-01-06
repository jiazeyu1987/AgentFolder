import { useEffect, useRef } from "react";

export function useInterval(callback: () => void, delayMs: number | null) {
  const cbRef = useRef(callback);
  cbRef.current = callback;

  useEffect(() => {
    if (delayMs === null) return;
    const t = setInterval(() => cbRef.current(), delayMs);
    return () => clearInterval(t);
  }, [delayMs]);
}


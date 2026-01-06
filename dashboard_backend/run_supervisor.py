from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional


RunAliveReason = str


@dataclass(frozen=True)
class RunStatus:
    alive: bool
    pid: Optional[int]
    job_id: Optional[str]
    started_at: Optional[str]
    reason: Optional[RunAliveReason] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "alive": bool(self.alive),
            "pid": self.pid,
            "job_id": self.job_id,
            "started_at": self.started_at,
        }
        if self.reason:
            out["reason"] = str(self.reason)
        return out


class RunSupervisor:
    def __init__(self, *, run_state_path: Path, is_process_alive: Callable[[int], bool]) -> None:
        self._path = run_state_path
        self._is_alive = is_process_alive

    def get_status(self, *, cleanup_stale: bool = False) -> RunStatus:
        if not self._path.exists():
            return RunStatus(alive=False, pid=None, job_id=None, started_at=None, reason="NO_STATE_FILE")

        try:
            state = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return RunStatus(alive=False, pid=None, job_id=None, started_at=None, reason="INVALID_STATE_JSON")

        pid = state.get("pid")
        job_id = state.get("job_id") if isinstance(state.get("job_id"), str) else None
        started_at = state.get("started_at") if isinstance(state.get("started_at"), str) else None

        if not isinstance(pid, int):
            return RunStatus(alive=False, pid=None, job_id=job_id, started_at=started_at, reason="INVALID_PID")

        alive = bool(self._is_alive(int(pid)))
        if alive:
            return RunStatus(alive=True, pid=int(pid), job_id=job_id, started_at=started_at)

        if cleanup_stale:
            try:
                self._path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except Exception:
                pass
        return RunStatus(alive=False, pid=int(pid), job_id=job_id, started_at=started_at, reason="PROCESS_DEAD")


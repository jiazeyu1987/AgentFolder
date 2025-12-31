from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from tkinter import END, BOTH, LEFT, X, filedialog, messagebox, scrolledtext, ttk
import tkinter as tk


ROOT_DIR = Path(__file__).resolve().parent
AGENT_CLI = ROOT_DIR / "agent_cli.py"


@dataclass(frozen=True)
class RunRequest:
    argv: list[str]
    cwd: Path


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Workflow Agent UI (agent_cli.py)")
        self.geometry("980x680")

        self._q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._running = False

        self._top_task_var = tk.StringVar(value="创建一个2048的游戏")
        self._max_attempts_var = tk.StringVar(value="3")
        self._max_iterations_var = tk.StringVar(value="20")
        self._plan_id_var = tk.StringVar(value="")
        self._task_id_var = tk.StringVar(value="")
        self._limit_var = tk.StringVar(value="50")
        self._db_path_var = tk.StringVar(value=str((ROOT_DIR / "state" / "state.db").resolve()))
        self._prompt_slot_var = tk.StringVar(value="AGENT:default:xiaobo")
        self._prompt_file_var = tk.StringVar(value="")

        self._build_ui()
        self.after(50, self._drain_queue)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill=BOTH, expand=True)

        top = ttk.Frame(outer)
        top.pack(fill=X)

        ttk.Label(top, text="DB:").pack(side=LEFT)
        ttk.Entry(top, textvariable=self._db_path_var, width=70).pack(side=LEFT, padx=6, fill=X, expand=True)
        ttk.Button(top, text="Browse...", command=self._pick_db).pack(side=LEFT)

        task_row = ttk.Frame(outer)
        task_row.pack(fill=X, pady=(10, 0))
        ttk.Label(task_row, text="Top task:").pack(side=LEFT)
        ttk.Entry(task_row, textvariable=self._top_task_var).pack(side=LEFT, padx=6, fill=X, expand=True)
        ttk.Button(task_row, text="Load from file...", command=self._pick_top_task_file).pack(side=LEFT)

        opts = ttk.Frame(outer)
        opts.pack(fill=X, pady=(10, 0))

        ttk.Label(opts, text="max-attempts").pack(side=LEFT)
        ttk.Entry(opts, textvariable=self._max_attempts_var, width=6).pack(side=LEFT, padx=(6, 16))

        ttk.Label(opts, text="max-iterations").pack(side=LEFT)
        ttk.Entry(opts, textvariable=self._max_iterations_var, width=6).pack(side=LEFT, padx=(6, 16))

        ttk.Label(opts, text="plan-id (optional)").pack(side=LEFT)
        ttk.Entry(opts, textvariable=self._plan_id_var, width=42).pack(side=LEFT, padx=6, fill=X, expand=True)

        filters = ttk.Frame(outer)
        filters.pack(fill=X, pady=(10, 0))
        ttk.Label(filters, text="task-id (optional)").pack(side=LEFT)
        ttk.Entry(filters, textvariable=self._task_id_var, width=46).pack(side=LEFT, padx=(6, 16), fill=X, expand=True)
        ttk.Label(filters, text="limit").pack(side=LEFT)
        ttk.Entry(filters, textvariable=self._limit_var, width=6).pack(side=LEFT, padx=6)

        buttons = ttk.Frame(outer)
        buttons.pack(fill=X, pady=(10, 0))

        ttk.Button(buttons, text="Create plan", command=self._cmd_create_plan).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="Run", command=self._cmd_run).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="Status", command=self._cmd_status).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="Events", command=self._cmd_events).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="Errors", command=self._cmd_errors).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="LLM log", command=self._cmd_llm_log).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="LLM calls (DB)", command=self._cmd_llm_calls).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="Doctor", command=self._cmd_doctor).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="Repair DB", command=self._cmd_repair_db).pack(side=LEFT, padx=(0, 6))
        ttk.Button(buttons, text="Reset FAILED/BLOCKED", command=self._cmd_reset_failed).pack(side=LEFT, padx=(0, 6))

        prompt_row = ttk.Frame(outer)
        prompt_row.pack(fill=X, pady=(10, 0))
        ttk.Label(prompt_row, text="prompt slot").pack(side=LEFT)
        ttk.Entry(prompt_row, textvariable=self._prompt_slot_var, width=28).pack(side=LEFT, padx=(6, 10))
        ttk.Label(prompt_row, text="file").pack(side=LEFT)
        ttk.Entry(prompt_row, textvariable=self._prompt_file_var, width=40).pack(side=LEFT, padx=6, fill=X, expand=True)
        ttk.Button(prompt_row, text="Pick...", command=self._pick_prompt_file).pack(side=LEFT, padx=(0, 6))
        ttk.Button(prompt_row, text="Prompt list", command=self._cmd_prompt_list).pack(side=LEFT, padx=(0, 6))
        ttk.Button(prompt_row, text="Prompt show", command=self._cmd_prompt_show).pack(side=LEFT, padx=(0, 6))
        ttk.Button(prompt_row, text="Prompt set(file)", command=self._cmd_prompt_set_file).pack(side=LEFT)

        tools = ttk.Frame(outer)
        tools.pack(fill=X, pady=(10, 0))
        ttk.Button(tools, text="Open workspace/required_docs", command=self._open_required_docs).pack(side=LEFT, padx=(0, 6))
        ttk.Button(tools, text="Open tasks/plan.json", command=self._open_plan_json).pack(side=LEFT, padx=(0, 6))
        ttk.Button(tools, text="Open logs/llm_runs.jsonl", command=self._open_llm_log_file).pack(side=LEFT, padx=(0, 6))
        ttk.Button(tools, text="Reset DB (delete)", command=self._cmd_reset_db).pack(side=LEFT, padx=(0, 6))
        ttk.Button(tools, text="Clear output", command=self._clear_output).pack(side=LEFT, padx=(0, 6))

        self._status = ttk.Label(outer, text=f"cwd: {ROOT_DIR}", foreground="#444")
        self._status.pack(fill=X, pady=(10, 4))

        out_frame = ttk.Frame(outer)
        out_frame.pack(fill=BOTH, expand=True)

        self._out = scrolledtext.ScrolledText(out_frame, height=20, wrap="word")
        self._out.pack(fill=BOTH, expand=True)
        self._out.insert(END, f"Using interpreter: {sys.executable}\n")
        self._out.insert(END, f"agent_cli.py: {AGENT_CLI}\n\n")

    def _pick_db(self) -> None:
        path = filedialog.askopenfilename(
            title="Select state.db",
            initialdir=str(ROOT_DIR / "state"),
            filetypes=[("SQLite DB", "*.db"), ("All files", "*.*")],
        )
        if path:
            self._db_path_var.set(path)

    def _pick_top_task_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select top-task file",
            initialdir=str(ROOT_DIR),
            filetypes=[("Text", "*.txt *.md"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Read failed", str(exc))
            return
        self._top_task_var.set(text.strip())

    def _pick_prompt_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select prompt markdown file",
            initialdir=str(ROOT_DIR),
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self._prompt_file_var.set(path)

    def _append(self, stream: str, text: str) -> None:
        self._out.insert(END, text)
        self._out.see(END)

    def _clear_output(self) -> None:
        self._out.delete("1.0", END)

    def _open_required_docs(self) -> None:
        path = ROOT_DIR / "workspace" / "required_docs"
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))  # type: ignore[attr-defined]

    def _open_plan_json(self) -> None:
        path = ROOT_DIR / "tasks" / "plan.json"
        if not path.exists():
            messagebox.showinfo("Not found", f"{path} does not exist yet.")
            return
        os.startfile(str(path))  # type: ignore[attr-defined]

    def _open_llm_log_file(self) -> None:
        path = ROOT_DIR / "logs" / "llm_runs.jsonl"
        if not path.exists():
            messagebox.showinfo("Not found", f"{path} does not exist yet.")
            return
        os.startfile(str(path))  # type: ignore[attr-defined]

    def _run(self, req: RunRequest) -> None:
        if self._running:
            messagebox.showwarning("Busy", "A command is already running.")
            return
        self._running = True
        self._status.configure(text="running: " + " ".join(req.argv))

        def worker() -> None:
            try:
                proc = subprocess.Popen(
                    req.argv,
                    cwd=str(req.cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    self._q.put(("stdout", line))
                code = proc.wait()
                self._q.put(("meta", f"\n[exit code: {code}]\n\n"))
            except Exception as exc:  # noqa: BLE001
                self._q.put(("meta", f"\n[failed to run: {type(exc).__name__}: {exc}]\n\n"))
            finally:
                self._q.put(("done", ""))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind in {"stdout", "meta"}:
                    self._append(kind, payload)
                if kind == "done":
                    self._running = False
                    self._status.configure(text=f"cwd: {ROOT_DIR}")
        except queue.Empty:
            pass
        self.after(50, self._drain_queue)

    def _base_argv(self) -> list[str]:
        if not AGENT_CLI.exists():
            raise RuntimeError(f"Missing {AGENT_CLI}")
        argv = [sys.executable, str(AGENT_CLI), "--db", self._db_path_var.get().strip()]
        return argv

    def _cmd_create_plan(self) -> None:
        top_task = self._top_task_var.get().strip()
        if not top_task:
            messagebox.showwarning("Missing", "Top task is empty.")
            return
        try:
            max_attempts = int(self._max_attempts_var.get().strip() or "3")
        except ValueError:
            messagebox.showwarning("Invalid", "max-attempts must be an integer.")
            return
        argv = self._base_argv() + ["create-plan", "--top-task", top_task, "--max-attempts", str(max_attempts)]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_run(self) -> None:
        try:
            max_iter = int(self._max_iterations_var.get().strip() or "20")
        except ValueError:
            messagebox.showwarning("Invalid", "max-iterations must be an integer.")
            return
        argv = self._base_argv() + ["run", "--max-iterations", str(max_iter)]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_status(self) -> None:
        argv = self._base_argv() + ["status"]
        plan_id = self._plan_id_var.get().strip()
        if plan_id:
            argv += ["--plan-id", plan_id]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_events(self) -> None:
        limit = self._limit_var.get().strip() or "50"
        argv = self._base_argv() + ["events", "--limit", str(limit)]
        plan_id = self._plan_id_var.get().strip()
        if plan_id:
            argv += ["--plan-id", plan_id]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_errors(self) -> None:
        limit = self._limit_var.get().strip() or "50"
        argv = self._base_argv() + ["errors", "--limit", str(limit)]
        plan_id = self._plan_id_var.get().strip()
        if plan_id:
            argv += ["--plan-id", plan_id]
        task_id = self._task_id_var.get().strip()
        if task_id:
            argv += ["--task-id", task_id]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_llm_log(self) -> None:
        limit = self._limit_var.get().strip() or "20"
        argv = self._base_argv() + ["llm-log", "--limit", str(limit)]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_llm_calls(self) -> None:
        limit = self._limit_var.get().strip() or "50"
        argv = self._base_argv() + ["llm-calls", "--limit", str(limit)]
        plan_id = self._plan_id_var.get().strip()
        if plan_id:
            argv += ["--plan-id", plan_id]
        task_id = self._task_id_var.get().strip()
        if task_id:
            argv += ["--task-id", task_id]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_doctor(self) -> None:
        argv = self._base_argv() + ["doctor"]
        plan_id = self._plan_id_var.get().strip()
        if plan_id:
            argv += ["--plan-id", plan_id]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_repair_db(self) -> None:
        ok = messagebox.askyesno("Confirm", "Run safe DB repairs (e.g., missing root task nodes)?")
        if not ok:
            return
        argv = self._base_argv() + ["repair-db"]
        plan_id = self._plan_id_var.get().strip()
        if plan_id:
            argv += ["--plan-id", plan_id]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_reset_failed(self) -> None:
        argv = self._base_argv() + ["reset-failed"]
        plan_id = self._plan_id_var.get().strip()
        if plan_id:
            argv += ["--plan-id", plan_id]
        # Default: when using the UI, you typically want to fully unstick tasks after changing prompts/config.
        argv += ["--include-blocked", "--reset-attempts"]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_prompt_list(self) -> None:
        argv = self._base_argv() + ["prompt", "list"]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_prompt_show(self) -> None:
        slot = self._prompt_slot_var.get().strip()
        if not slot:
            messagebox.showwarning("Missing", "prompt slot is empty.")
            return
        argv = self._base_argv() + ["prompt", "show", slot]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_prompt_set_file(self) -> None:
        slot = self._prompt_slot_var.get().strip()
        if not slot:
            messagebox.showwarning("Missing", "prompt slot is empty.")
            return
        file_path = self._prompt_file_var.get().strip()
        if not file_path:
            messagebox.showwarning("Missing", "Pick a prompt file first.")
            return
        argv = self._base_argv() + ["prompt", "set", slot, "--file", file_path]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_reset_db(self) -> None:
        db_path = self._db_path_var.get().strip()
        if not db_path:
            messagebox.showwarning("Missing", "DB path is empty.")
            return
        ok = messagebox.askyesno("Confirm", f"Delete ALL DB data?\n\n{db_path}\n\nThis removes the DB file.")
        if not ok:
            return
        argv = self._base_argv() + ["reset-db"]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import queue
import sqlite3
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


class LLMExplorer(tk.Toplevel):
    def __init__(self, *, parent: tk.Tk, db_path: str) -> None:
        super().__init__(parent)
        self.title("LLM Explorer (llm_calls)")
        self.geometry("1120x760")

        self._db_path = db_path

        self._plan_id_var = tk.StringVar(value="")
        self._plan_title_var = tk.StringVar(value="")
        self._agent_var = tk.StringVar(value="")
        self._scope_var = tk.StringVar(value="")
        self._limit_var = tk.StringVar(value="200")
        self._errors_only_var = tk.BooleanVar(value=False)

        self._selected_llm_call_id: str | None = None

        self._build_ui()
        self._refresh()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill=BOTH, expand=True)

        filters1 = ttk.Frame(outer)
        filters1.pack(fill=X)
        ttk.Label(filters1, text="plan-id").pack(side=LEFT)
        ttk.Entry(filters1, textvariable=self._plan_id_var, width=44).pack(side=LEFT, padx=(6, 14))
        ttk.Label(filters1, text="plan-title contains").pack(side=LEFT)
        ttk.Entry(filters1, textvariable=self._plan_title_var, width=40).pack(side=LEFT, padx=(6, 14), fill=X, expand=True)

        filters2 = ttk.Frame(outer)
        filters2.pack(fill=X, pady=(6, 0))
        ttk.Label(filters2, text="agent").pack(side=LEFT)
        ttk.Entry(filters2, textvariable=self._agent_var, width=10).pack(side=LEFT, padx=(6, 14))
        ttk.Label(filters2, text="scope").pack(side=LEFT)
        ttk.Entry(filters2, textvariable=self._scope_var, width=18).pack(side=LEFT, padx=(6, 14))
        ttk.Label(filters2, text="limit").pack(side=LEFT)
        ttk.Entry(filters2, textvariable=self._limit_var, width=6).pack(side=LEFT, padx=(6, 14))
        ttk.Checkbutton(filters2, text="errors only", variable=self._errors_only_var).pack(side=LEFT)
        ttk.Button(filters2, text="Search", command=self._refresh).pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(outer, orient="vertical")
        paned.pack(fill=BOTH, expand=True, pady=(10, 0))

        top = ttk.Frame(paned)
        bottom = ttk.Frame(paned)
        paned.add(top, weight=1)
        paned.add(bottom, weight=2)

        self._tree = ttk.Treeview(
            top,
            columns=("created_at", "scope", "agent", "plan_title", "plan_id", "task_title", "task_id", "error_code", "validator_error"),
            show="headings",
            height=12,
        )
        for col, w in [
            ("created_at", 160),
            ("scope", 140),
            ("agent", 80),
            ("plan_title", 180),
            ("plan_id", 240),
            ("task_title", 220),
            ("task_id", 240),
            ("error_code", 120),
            ("validator_error", 220),
        ]:
            self._tree.heading(col, text=col)
            self._tree.column(col, width=w, stretch=(col in {"validator_error"}))
        self._tree.pack(fill=BOTH, expand=True, side=LEFT)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        yscroll = ttk.Scrollbar(top, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscroll=yscroll.set)
        yscroll.pack(side=LEFT, fill="y")

        details_top = ttk.Frame(bottom)
        details_top.pack(fill=X)
        ttk.Button(details_top, text="Copy prompt", command=self._copy_prompt).pack(side=LEFT, padx=(0, 6))
        ttk.Button(details_top, text="Copy response", command=self._copy_response).pack(side=LEFT, padx=(0, 6))
        ttk.Button(details_top, text="Save prompt...", command=self._save_prompt).pack(side=LEFT, padx=(0, 6))
        ttk.Button(details_top, text="Save response...", command=self._save_response).pack(side=LEFT, padx=(0, 6))

        nb = ttk.Notebook(bottom)
        nb.pack(fill=BOTH, expand=True, pady=(8, 0))

        self._prompt_text = scrolledtext.ScrolledText(nb, wrap="word")
        self._resp_text = scrolledtext.ScrolledText(nb, wrap="word")
        self._parsed_text = scrolledtext.ScrolledText(nb, wrap="word")
        self._norm_text = scrolledtext.ScrolledText(nb, wrap="word")
        self._meta_text = scrolledtext.ScrolledText(nb, wrap="word")

        nb.add(self._prompt_text, text="Prompt")
        nb.add(self._resp_text, text="Raw Response")
        nb.add(self._parsed_text, text="Parsed JSON")
        nb.add(self._norm_text, text="Normalized JSON")
        nb.add(self._meta_text, text="Meta/Errors")

    def _refresh(self) -> None:
        for iid in self._tree.get_children():
            self._tree.delete(iid)

        try:
            limit = int(self._limit_var.get().strip() or "200")
        except ValueError:
            limit = 200

        plan_id_filter = self._plan_id_var.get().strip()
        plan_title_filter = self._plan_title_var.get().strip().lower()
        agent = self._agent_var.get().strip()
        scope = self._scope_var.get().strip()
        errors_only = bool(self._errors_only_var.get())

        where: list[str] = []
        params: list[object] = []
        if agent:
            where.append("c.agent = ?")
            params.append(agent)
        if scope:
            where.append("c.scope = ?")
            params.append(scope)
        if errors_only:
            where.append("(c.error_code IS NOT NULL OR c.validator_error IS NOT NULL)")
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        try:
            conn = self._connect()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("DB error", str(exc))
            return

        try:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_calls'").fetchone()
            if not exists:
                messagebox.showerror("Not found", "llm_calls table not found. Run migrations / update repo.")
                return

            rows = conn.execute(
                f"""
                SELECT
                  c.llm_call_id,
                  c.created_at,
                  c.scope,
                  c.agent,
                  c.plan_id,
                  p.title AS plan_title,
                  c.task_id,
                  n.title AS task_title,
                  c.error_code,
                  c.validator_error,
                  c.parsed_json,
                  c.normalized_json
                FROM llm_calls c
                LEFT JOIN plans p ON p.plan_id = c.plan_id
                LEFT JOIN task_nodes n ON n.task_id = c.task_id
                {where_sql}
                ORDER BY c.created_at DESC
                LIMIT ?
                """,
                (*params, int(max(limit * 5, limit, 200))),
            ).fetchall()
        finally:
            conn.close()

        def derive_plan_fields(row: sqlite3.Row) -> tuple[str | None, str | None]:
            pid = row["plan_id"]
            ptitle = row["plan_title"]
            if pid and ptitle:
                return pid, ptitle

            # For PLAN_GEN records, plan_id may be NULL at insert-time. Try to derive from JSON payload.
            for field in ("normalized_json", "parsed_json"):
                txt = row[field]
                if not txt or not isinstance(txt, str):
                    continue
                try:
                    obj = json.loads(txt)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                # normalized_json is already plan_json, parsed_json may be an outer wrapper {schema_version, plan_json}.
                plan_obj = obj
                if "plan" not in plan_obj and isinstance(obj.get("plan_json"), dict):
                    plan_obj = obj.get("plan_json")  # type: ignore[assignment]
                if not isinstance(plan_obj, dict):
                    continue
                meta = plan_obj.get("plan")
                if not isinstance(meta, dict):
                    continue
                pid2 = meta.get("plan_id")
                title2 = meta.get("title")
                if isinstance(pid2, str) and pid2.strip():
                    if not pid:
                        pid = pid2.strip()
                    if not ptitle and isinstance(title2, str) and title2.strip():
                        ptitle = title2.strip()
            return pid, ptitle

        def passes_filters(row: sqlite3.Row) -> bool:
            pid, ptitle = derive_plan_fields(row)
            if plan_id_filter and (not pid or pid != plan_id_filter):
                return False
            if plan_title_filter:
                t = (ptitle or "").lower()
                if plan_title_filter not in t:
                    return False
            return True

        filtered_rows = [r for r in rows if passes_filters(r)]
        filtered_rows = filtered_rows[:limit]

        for r in filtered_rows:
            ve = r["validator_error"]
            if isinstance(ve, str) and len(ve) > 120:
                ve = ve[:120] + "..."
            plan_id_val, plan_title = derive_plan_fields(r)
            if not plan_title and plan_id_val:
                plan_title = "(plan title unknown)"
            task_title = r["task_title"]
            if not task_title and not r["task_id"] and str(r["scope"] or "").startswith("PLAN_"):
                task_title = "(PLAN)"
            self._tree.insert(
                "",
                "end",
                iid=r["llm_call_id"],
                values=(
                    r["created_at"],
                    r["scope"],
                    r["agent"],
                    plan_title,
                    plan_id_val,
                    task_title,
                    r["task_id"],
                    r["error_code"],
                    ve,
                ),
            )

        # Clear detail panels on refresh.
        self._selected_llm_call_id = None
        for box in (self._prompt_text, self._resp_text, self._parsed_text, self._norm_text, self._meta_text):
            box.delete("1.0", END)

    def _on_select(self, _evt: object) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        llm_call_id = sel[0]
        self._selected_llm_call_id = llm_call_id
        # Auto-fill plan-id/plan-title fields from the selected row for easier filtering.
        try:
            vals = self._tree.item(llm_call_id, "values") or []
            if len(vals) >= 5:
                plan_title = str(vals[3] or "")
                plan_id = str(vals[4] or "")
                if plan_id:
                    self._plan_id_var.set(plan_id)
                if plan_title and plan_title != "(plan title unknown)":
                    self._plan_title_var.set(plan_title)
        except Exception:
            pass
        self._load_details(llm_call_id)

    def _load_details(self, llm_call_id: str) -> None:
        try:
            conn = self._connect()
            row = conn.execute(
                """
                SELECT
                  llm_call_id, created_at, started_at_ts, finished_at_ts,
                  plan_id, task_id, agent, scope, provider,
                  error_code, error_message, validator_error,
                  prompt_text, response_text, parsed_json, normalized_json, meta_json
                FROM llm_calls
                WHERE llm_call_id = ?
                """,
                (llm_call_id,),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Query failed", str(exc))
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if not row:
            return

        plan_title = None
        task_title = None
        try:
            conn2 = self._connect()
            if row["plan_id"]:
                r2 = conn2.execute("SELECT title FROM plans WHERE plan_id=?", (row["plan_id"],)).fetchone()
                if r2:
                    plan_title = r2["title"]
            if row["task_id"]:
                r3 = conn2.execute("SELECT title FROM task_nodes WHERE task_id=?", (row["task_id"],)).fetchone()
                if r3:
                    task_title = r3["title"]
        except Exception:
            plan_title = plan_title
            task_title = task_title
        finally:
            try:
                conn2.close()
            except Exception:
                pass

        def set_box(box: scrolledtext.ScrolledText, text: str) -> None:
            box.delete("1.0", END)
            box.insert(END, text or "")
            box.see("1.0")

        prompt = row["prompt_text"] or ""
        resp = row["response_text"] or ""
        parsed = row["parsed_json"] or ""
        norm = row["normalized_json"] or ""

        meta = {
            "llm_call_id": row["llm_call_id"],
            "created_at": row["created_at"],
            "started_at_ts": row["started_at_ts"],
            "finished_at_ts": row["finished_at_ts"],
            "plan_id": row["plan_id"],
            "plan_title": plan_title,
            "task_id": row["task_id"],
            "task_title": task_title,
            "agent": row["agent"],
            "scope": row["scope"],
            "provider": row["provider"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "validator_error": row["validator_error"],
        }
        extra = row["meta_json"]
        if extra:
            try:
                meta["meta_json"] = json.loads(extra)
            except Exception:
                meta["meta_json"] = extra

        def pretty_json(text: str) -> str:
            try:
                obj = json.loads(text)
            except Exception:
                return text
            try:
                return json.dumps(obj, ensure_ascii=False, indent=2)
            except Exception:
                return text

        set_box(self._prompt_text, prompt)
        set_box(self._resp_text, resp)
        set_box(self._parsed_text, pretty_json(parsed))
        set_box(self._norm_text, pretty_json(norm))
        set_box(self._meta_text, json.dumps(meta, ensure_ascii=False, indent=2))

    def _copy_prompt(self) -> None:
        text = self._prompt_text.get("1.0", END).strip()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)

    def _copy_response(self) -> None:
        text = self._resp_text.get("1.0", END).strip()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)

    def _save_prompt(self) -> None:
        text = self._prompt_text.get("1.0", END)
        if not text.strip():
            return
        path = filedialog.asksaveasfilename(title="Save prompt", initialdir=str(ROOT_DIR), defaultextension=".txt")
        if path:
            Path(path).write_text(text, encoding="utf-8")

    def _save_response(self) -> None:
        text = self._resp_text.get("1.0", END)
        if not text.strip():
            return
        path = filedialog.asksaveasfilename(title="Save response", initialdir=str(ROOT_DIR), defaultextension=".txt")
        if path:
            Path(path).write_text(text, encoding="utf-8")


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
        ttk.Button(tools, text="LLM Explorer", command=self._open_llm_explorer).pack(side=LEFT, padx=(0, 6))
        ttk.Button(tools, text="Export Deliverables", command=self._cmd_export_deliverables).pack(side=LEFT, padx=(0, 6))
        tk.Button(
            tools,
            text="Reset DB (delete)",
            command=self._cmd_reset_db,
            bg="#b00020",
            fg="white",
            activebackground="#d32f2f",
            activeforeground="white",
            relief="raised",
        ).pack(side=LEFT, padx=(0, 6))
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

    def _open_llm_explorer(self) -> None:
        db_path = self._db_path_var.get().strip()
        if not db_path:
            messagebox.showwarning("Missing", "DB path is empty.")
            return
        if not Path(db_path).exists():
            messagebox.showwarning("Not found", f"{db_path} does not exist.")
            return
        LLMExplorer(parent=self, db_path=db_path)

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
        purge = messagebox.askyesno("Also purge files?", "Also delete workspace/*, tasks/*, and logs/* contents?\n\nThis removes all generated artifacts, reviews, required_docs, inputs, plan.json, and llm logs.")
        argv = self._base_argv() + ["reset-db"]
        if purge:
            argv += ["--purge-workspace", "--purge-tasks", "--purge-logs"]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))

    def _cmd_export_deliverables(self) -> None:
        argv = self._base_argv() + ["export"]
        plan_id = self._plan_id_var.get().strip()
        if plan_id:
            argv += ["--plan-id", plan_id]
        self._append("meta", "\n$ " + " ".join(argv) + "\n")
        self._run(RunRequest(argv=argv, cwd=ROOT_DIR))


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

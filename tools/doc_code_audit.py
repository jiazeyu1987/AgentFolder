from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DOC_DIR = ROOT / "doc" / "code"
AGENT_CLI = ROOT / "agent_cli.py"


BACKTICK_RE = re.compile(r"`([^`]+)`")
CREATE_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)


@dataclass(frozen=True)
class Issue:
    doc: Path
    message: str


def _run(argv: list[str]) -> str:
    proc = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (proc.stdout or "") + (proc.stderr or "")


def _extract_backticks(text: str) -> list[str]:
    return [m.group(1).strip() for m in BACKTICK_RE.finditer(text) if m.group(1).strip()]


def _load_tables_from_migrations() -> set[str]:
    tables: set[str] = set()
    mig_dir = ROOT / "state" / "migrations"
    for p in sorted(mig_dir.glob("*.sql")):
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in CREATE_TABLE_RE.finditer(t):
            tables.add(m.group(1))
    return tables


def _parse_allowed_set_from_contracts(var_name: str) -> set[str]:
    text = (ROOT / "core" / "contracts.py").read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^{re.escape(var_name)}\s*=\s*\{{([^}}]+)\}}", text, flags=re.MULTILINE)
    if not m:
        return set()
    inner = m.group(1)
    items: set[str] = set()
    for s in re.findall(r"'([^']+)'", inner):
        items.add(s)
    for s in re.findall(r'"([^"]+)"', inner):
        items.add(s)
    return items


def _is_repo_path_token(tok: str) -> bool:
    if "::" in tok:
        return False
    if tok.startswith(("workspace/", "tasks/", "state/", "logs/", "core/", "doc/", "agents/", "skills/")):
        return True
    if tok.endswith((".py", ".md", ".sql", ".json", ".yaml", ".yml")) and ("/" in tok or "\\" in tok):
        return True
    if tok in {"agent_cli.py", "agent_ui.py", "run.py", "config.py", "shared_prompt.md", "runtime_config.json"}:
        return True
    return False


def _check_path_exists(tok: str) -> bool:
    # Normalize slashes; keep Windows drive paths as-is.
    if re.match(r"^[a-zA-Z]:\\", tok):
        return True  # we don't assert local machine absolute paths exist
    rel = tok.replace("\\", "/")
    # Handle globs and placeholders like workspace/deliverables/<plan_id>/
    if "*" in rel or "<" in rel or ">" in rel:
        prefix = rel.split("*", 1)[0]
        prefix = prefix.split("<", 1)[0]
        prefix = prefix.rstrip("/")
        if not prefix:
            return True
        return (ROOT / prefix).exists()
    p = ROOT / rel
    return p.exists()


def _is_cli_command_token(tok: str) -> bool:
    return "agent_cli.py" in tok and (" " in tok or tok.endswith("agent_cli.py"))


def _extract_cli_subcommand(tok: str) -> str | None:
    parts = [p for p in re.split(r"\s+", tok.strip()) if p]
    if "agent_cli.py" not in parts:
        # maybe "D:\miniconda3\python.exe agent_cli.py ..."
        try:
            idx = parts.index("agent_cli.py")
        except ValueError:
            return None
    else:
        idx = parts.index("agent_cli.py")
    if idx + 1 >= len(parts):
        return None
    sub = parts[idx + 1]
    if sub.startswith("-"):
        return None
    return sub


def _options_in_help(sub: str) -> set[str]:
    out = _run([sys.executable, str(AGENT_CLI), sub, "--help"])
    opts: set[str] = set()
    for m in re.finditer(r"(?m)^\s{2}(--[a-z0-9-]+)", out):
        opts.add(m.group(1))
    return opts


def _mentioned_options(tok: str) -> set[str]:
    # Extract flags from the token itself: `--foo` occurrences.
    return set(re.findall(r"(--[a-z0-9-]+)", tok))


def audit() -> list[Issue]:
    issues: list[Issue] = []
    if not DOC_DIR.exists():
        return [Issue(doc=DOC_DIR, message="doc/code directory missing")]

    tables = _load_tables_from_migrations()
    allowed_formats = _parse_allowed_set_from_contracts("ALLOWED_ARTIFACT_FORMATS")
    allowed_edge_types = _parse_allowed_set_from_contracts("ALLOWED_EDGE_TYPES")
    allowed_node_types = _parse_allowed_set_from_contracts("ALLOWED_NODE_TYPES")
    allowed_agents = _parse_allowed_set_from_contracts("ALLOWED_AGENTS")

    contracts_text = (DOC_DIR / "Contracts.md").read_text(encoding="utf-8", errors="replace") if (DOC_DIR / "Contracts.md").exists() else ""
    # Cross-check: doc lists should match code enums (best-effort).
    def _extract_pipe_list(label: str) -> set[str] | None:
        # Example: "当前：`md|txt|json|html|css|js`"
        m = re.search(rf"{re.escape(label)}.*?`([^`]+)`", contracts_text, flags=re.IGNORECASE)
        if not m:
            return None
        items = {x.strip() for x in m.group(1).split("|") if x.strip()}
        return items or None

    listed_formats = _extract_pipe_list("当前")
    if listed_formats is not None:
        # Only compare against the set of string items in the constant.
        if allowed_formats and listed_formats != allowed_formats:
            issues.append(
                Issue(
                    doc=DOC_DIR / "Contracts.md",
                    message=f"artifact formats mismatch: doc={sorted(listed_formats)}, code={sorted(allowed_formats)}",
                )
            )

    cli_help = _run([sys.executable, str(AGENT_CLI), "--help"])
    available_cmds = set(re.findall(r"(?m)^\s{4}([a-z0-9-]+)\s{2,}", cli_help))

    def check_contract_symbol(symbol: str) -> bool:
        # supports forms like `core/contracts.py::normalize_plan_json`
        if "::" not in symbol:
            return False
        file_part, name = symbol.split("::", 1)
        rel = file_part.replace("\\", "/")
        p = ROOT / rel
        if not p.exists():
            return False
        text = p.read_text(encoding="utf-8", errors="replace")
        if bool(re.search(rf"^def\s+{re.escape(name)}\b", text, flags=re.MULTILINE)):
            return True
        # Also allow constants like `ALLOWED_ARTIFACT_FORMATS = {...}`
        return bool(re.search(rf"^{re.escape(name)}\s*=", text, flags=re.MULTILINE))

    for doc_path in sorted(DOC_DIR.glob("*.md")):
        text = doc_path.read_text(encoding="utf-8", errors="replace")
        tokens = _extract_backticks(text)
        for tok in tokens:
            if _is_cli_command_token(tok):
                sub = _extract_cli_subcommand(tok)
                if not sub:
                    continue
                if sub not in available_cmds:
                    issues.append(Issue(doc=doc_path, message=f"unknown CLI subcommand in doc: {sub!r} (from `{tok}`)"))
                    continue
                mentioned = _mentioned_options(tok)
                if mentioned:
                    opts = _options_in_help(sub)
                    for opt in sorted(mentioned):
                        if opt not in opts:
                            issues.append(Issue(doc=doc_path, message=f"unknown CLI option for {sub}: {opt} (from `{tok}`)"))

            if _is_repo_path_token(tok):
                if not _check_path_exists(tok):
                    issues.append(Issue(doc=doc_path, message=f"referenced path does not exist: `{tok}`"))

            if tok in tables:
                continue
            if tok in {"plans", "task_nodes", "task_edges", "artifacts", "reviews", "task_events", "llm_calls", "input_requirements", "evidences", "input_files"}:
                if tok not in tables:
                    issues.append(Issue(doc=doc_path, message=f"referenced DB table not found in migrations: `{tok}`"))

            if tok.startswith("core/") and "::" in tok:
                if not check_contract_symbol(tok):
                    issues.append(Issue(doc=doc_path, message=f"referenced symbol not found: `{tok}`"))

        # Non-backtick consistency checks (lightweight)
        if "input_evidence" in text:
            issues.append(Issue(doc=doc_path, message="uses old table name `input_evidence` (should be `evidences`)"))

    # Sanity: docs mention allowed node/edge types that match contracts.
    # (We only check that the sets are non-empty and expected literals exist in code.)
    if not allowed_formats:
        issues.append(Issue(doc=DOC_DIR / "Contracts.md", message="could not parse ALLOWED_ARTIFACT_FORMATS from core/contracts.py"))
    if not allowed_edge_types or not allowed_node_types or not allowed_agents:
        issues.append(Issue(doc=DOC_DIR / "Contracts.md", message="could not parse ALLOWED_* sets from core/contracts.py"))

    return issues


def main(argv: list[str]) -> int:
    _ = argv
    issues = audit()
    if not issues:
        print("doc/code audit: OK")
        return 0
    print("doc/code audit: FAILED")
    for i in issues:
        rel = i.doc.relative_to(ROOT) if i.doc.exists() else i.doc
        print(f"- {rel}: {i.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

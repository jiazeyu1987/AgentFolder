from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import config


CASES_DIR = config.ROOT_DIR / "tests" / "fixtures" / "cases"


@dataclass(frozen=True)
class FixtureCase:
    case_id: str
    top_task: str
    expected_outcome: str
    notes: str
    recommended_commands: List[str]
    case_dir: Path


def list_cases(cases_dir: Path = CASES_DIR) -> List[str]:
    if not cases_dir.exists():
        return []
    return sorted([p.name for p in cases_dir.iterdir() if p.is_dir()])


def load_case(case_id: str, cases_dir: Path = CASES_DIR) -> FixtureCase:
    d = cases_dir / case_id
    meta_path = d / "case.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"case.json not found for case_id={case_id}: {meta_path}")
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("case.json must be an object")
    cid = str(data.get("case_id") or "").strip()
    if cid != case_id:
        raise ValueError(f"case.json.case_id mismatch: expected {case_id}, got {cid!r}")
    top_task = str(data.get("top_task") or "").strip()
    expected = str(data.get("expected_outcome") or "").strip()
    notes = str(data.get("notes") or "").strip()
    cmds = data.get("recommended_commands") or []
    if not isinstance(cmds, list) or any(not isinstance(x, str) for x in cmds):
        cmds = []
    return FixtureCase(
        case_id=case_id,
        top_task=top_task,
        expected_outcome=expected,
        notes=notes,
        recommended_commands=[str(x) for x in cmds],
        case_dir=d,
    )


def install_case(case_id: str, *, dest_dir: Path = config.BASELINE_INPUTS_DIR, cases_dir: Path = CASES_DIR) -> Path:
    """
    Copy a case's baseline_inputs into dest_dir/<case_id>/...
    """
    case = load_case(case_id, cases_dir=cases_dir)
    src = case.case_dir / "baseline_inputs"
    if not src.exists():
        raise FileNotFoundError(f"baseline_inputs not found for case_id={case_id}: {src}")
    dest_dir = Path(dest_dir)
    dest = dest_dir / case_id
    dest.mkdir(parents=True, exist_ok=True)
    # Copy files, keeping relative structure, but do not delete any existing content.
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return dest


def install_all_cases(*, dest_dir: Path = config.BASELINE_INPUTS_DIR, cases_dir: Path = CASES_DIR) -> List[Path]:
    out: List[Path] = []
    for cid in list_cases(cases_dir=cases_dir):
        out.append(install_case(cid, dest_dir=dest_dir, cases_dir=cases_dir))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Install reproducible S/M/L fixture baseline_inputs (P0.4).")
    p.add_argument("--case", dest="case_id", type=str, help="Case ID to install (e.g., S_2048)")
    p.add_argument("--all", action="store_true", help="Install all cases")
    p.add_argument("--dest", type=str, default=str(config.BASELINE_INPUTS_DIR), help="Destination baseline_inputs directory")
    p.add_argument("--list", action="store_true", help="List available cases")
    args = p.parse_args(list(argv) if argv is not None else None)

    if args.list:
        for cid in list_cases():
            print(cid)
        return 0

    dest = Path(args.dest)
    if args.all:
        paths = install_all_cases(dest_dir=dest)
        for pth in paths:
            print(str(pth))
        return 0

    if args.case_id:
        pth = install_case(args.case_id, dest_dir=dest)
        print(str(pth))
        return 0

    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


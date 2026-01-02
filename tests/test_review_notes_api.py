from pathlib import Path

import dashboard_backend.app as backend


def test_prompt_file_allows_review_notes_dir(tmp_path: Path, monkeypatch) -> None:
    notes_dir = tmp_path / "review_notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    p = notes_dir / "x" / "plan_review_attempt_1.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("hello", encoding="utf-8")

    monkeypatch.setattr(backend.config, "REVIEW_NOTES_DIR", notes_dir)

    res = backend._safe_read_text_file(str(p), max_chars=1000)  # type: ignore[attr-defined]
    assert res["content"] == "hello"


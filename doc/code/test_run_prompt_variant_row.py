import sqlite3


def _row(attempt_count: int, status: str) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE t (attempt_count INTEGER, status TEXT)")
    cur.execute("INSERT INTO t (attempt_count, status) VALUES (?, ?)", (attempt_count, status))
    return cur.execute("SELECT attempt_count, status FROM t").fetchone()


def test_task_prompt_variant_accepts_sqlite_row():
    from run import _task_prompt_variant

    assert _task_prompt_variant(_row(0, "READY")) == "TASK_ACTION_INIT"
    assert _task_prompt_variant(_row(1, "READY")) == "TASK_ACTION_ITERATE"
    assert _task_prompt_variant(_row(0, "TO_BE_MODIFY")) == "TASK_ACTION_ITERATE"


def test_task_prompt_variant_accepts_dict():
    from run import _task_prompt_variant

    assert _task_prompt_variant({"attempt_count": 0, "status": "READY"}) == "TASK_ACTION_INIT"
    assert _task_prompt_variant({"attempt_count": 2, "status": "READY"}) == "TASK_ACTION_ITERATE"


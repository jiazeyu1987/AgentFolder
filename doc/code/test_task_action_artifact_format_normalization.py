def test_task_action_artifact_format_text_html_normalizes():
    from core.contracts_v2 import normalize_and_validate

    raw = {
        "schema_version": "xiaobo_action_v1",
        "task_id": "t1",
        "result_type": "ARTIFACT",
        "artifact": {"name": "a", "format": "text/html", "content": "<h1>x</h1>"},
    }
    normalized, err = normalize_and_validate("TASK_ACTION", raw, {"task_id": "t1"})

    assert err is None
    assert isinstance(normalized, dict)
    assert normalized["result_type"] == "ARTIFACT"
    assert normalized["artifact"]["format"] == "html"


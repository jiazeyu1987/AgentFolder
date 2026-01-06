def test_task_action_schema_version_semver_normalizes():
    from core.contracts_v2 import normalize_and_validate

    raw = {"schema_version": "1.0.0", "task_id": "t1", "result_type": "NOOP"}
    normalized, err = normalize_and_validate("TASK_ACTION", raw, {"task_id": "t1"})

    assert err is None
    assert isinstance(normalized, dict)
    assert normalized["schema_version"] == "xiaobo_action_v1"


from core.state_machine.plan_stage import clamp_resume_stage, decide_next_stage, normalize_stage


def test_decide_next_stage_rules():
    assert decide_next_stage(current_stage="STRUCTURE", score=90, pass_score=60).decision == "ADVANCE"
    assert decide_next_stage(current_stage="STRUCTURE", score=90, pass_score=60).stage == "BINDINGS"

    assert decide_next_stage(current_stage="BINDINGS", score=59, pass_score=60).decision == "RETRY"
    assert decide_next_stage(current_stage="BINDINGS", score=59, pass_score=60).stage == "BINDINGS"

    assert decide_next_stage(current_stage="EXECUTION", score=60, pass_score=60).decision == "DONE"
    assert decide_next_stage(current_stage="EXECUTION", score=60, pass_score=60).stage == "EXECUTION"


def test_clamp_resume_stage_never_skips_unpassed():
    # Unknown resumes normalize to STRUCTURE
    assert normalize_stage("whatever") == "STRUCTURE"

    # Can't jump to bindings unless STRUCTURE passed
    assert clamp_resume_stage(resume_stage="BINDINGS", passed={"STRUCTURE": False, "BINDINGS": False}) == "STRUCTURE"

    # Can't jump to execution unless BINDINGS passed
    assert clamp_resume_stage(resume_stage="EXECUTION", passed={"STRUCTURE": True, "BINDINGS": False}) == "BINDINGS"


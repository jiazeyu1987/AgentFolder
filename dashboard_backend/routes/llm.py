from fastapi import APIRouter

from dashboard_backend import app_helpers as h

router = APIRouter()

router.get("/api/task/{task_id}/llm")(h.get_task_llm_calls)
router.get("/api/llm_calls")(h.get_llm_calls)
router.get("/api/workflow", response_model=h.WorkflowV1Resp)(h.get_workflow)
router.get("/api/task/{task_id}/details")(h.get_task_details)


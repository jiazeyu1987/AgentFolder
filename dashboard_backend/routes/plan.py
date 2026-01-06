from fastapi import APIRouter

from dashboard_backend import app_helpers as h

router = APIRouter()

router.get("/api/plans")(h.get_plans)
router.get("/api/plan/{plan_id}/graph", response_model=h.GraphV1Resp)(h.get_plan_graph)
router.get("/api/plan_snapshot")(h.plan_snapshot)
router.post("/api/plan/create_async")(h.create_plan_async)
router.get("/api/jobs/{job_id}")(h.get_job)
router.get("/api/job_log")(h.get_job_log)
router.post("/api/plan/create")(h.create_plan)
router.post("/api/reset-to-plan")(h.reset_to_plan)


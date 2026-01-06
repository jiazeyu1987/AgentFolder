from fastapi import APIRouter

from dashboard_backend import app_helpers as h

router = APIRouter()

router.post("/api/run/start")(h.run_start)
router.post("/api/run/stop")(h.run_stop)
router.post("/api/run/once")(h.run_once)
router.get("/api/run/status")(h.run_status)


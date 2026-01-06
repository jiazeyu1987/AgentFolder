from fastapi import APIRouter

from dashboard_backend import app_helpers as h

router = APIRouter()

router.get("/api/config")(h.get_config)
router.get("/api/errors")(h.get_errors)
router.get("/api/audit")(h.get_audit)
router.get("/api/top_tasks")(h.get_top_tasks)
router.post("/api/runtime_config/update")(h.update_runtime_config)
router.get("/api/prompt_file")(h.get_prompt_file)
router.post("/api/reset-failed")(h.reset_failed)
router.post("/api/reset-db")(h.reset_db)
router.post("/api/export")(h.export)


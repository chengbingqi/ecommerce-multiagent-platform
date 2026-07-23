from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.demo import router as demo_router
from app.api.v1.health import router as health_router
from app.api.v1.media import router as media_router
from app.api.v1.runs import router as runs_router

router = APIRouter(prefix="/api/v1")
router.include_router(health_router)
router.include_router(auth_router)
router.include_router(demo_router)
router.include_router(runs_router)
router.include_router(media_router)

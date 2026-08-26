from fastapi import APIRouter
from app.api.endpoints import health, pipeline

router = APIRouter()

router.include_router(health.router)
router.include_router(pipeline.router)

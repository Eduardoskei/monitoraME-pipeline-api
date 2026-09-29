from fastapi import APIRouter
from app.api.endpoints import analysis, health, pipeline

router = APIRouter()

router.include_router(health.router)
router.include_router(pipeline.router)

router.include_router(analysis.router)

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core import database
from app.api.endpoints.health import health
from app.api.endpoints.pipeline import (
    pncp_contratacoes,
    tce_contratos,
    tce_kpi_me_por_mes,
)
from app.api.router import router
from app.pipeline import analisys

__all__ = [
    "analisys",
    "app",
    "health",
    "lifespan",
    "pncp_contratacoes",
    "tce_contratos",
    "tce_kpi_me_por_mes",
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        database.init_db()
        yield
    finally:
        database.close_pool()


app = FastAPI(
    title="Monitoramento de Contas Publicas",
    description="API para consulta e analise de contratacoes publicas a partir do PNCP e TCE-CE.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)

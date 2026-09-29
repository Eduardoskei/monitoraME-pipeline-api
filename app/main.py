from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core import database, log_database
from app.api.endpoints.health import health
from app.api.endpoints.pipeline import (
    tce_analitico_indicadores,
    tce_contratos,
    tce_kpi_portes_por_mes,
)
from app.api.router import router
from app.pipeline import analisys

__all__ = [
    "analisys",
    "app",
    "health",
    "lifespan",
    "tce_analitico_indicadores",
    "tce_contratos",
    "tce_kpi_portes_por_mes",
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        database.init_db()
        log_database.init_log_db()
        yield
    finally:
        database.close_pool()
        log_database.close_log_pool()


app = FastAPI(
    title="Monitoramento de Contas Publicas",
    description="API do modulo analitico retrospectivo de despesas empenhadas do TCE-CE.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.api.errors import resposta_erro_validacao, usa_erro_padronizado
from app.core import database, log_database
from app.core.logging import (
    configurar_logging,
    desabilitar_persistencia_logs,
    habilitar_persistencia_logs,
)
from app.api.endpoints.health import health
from app.api.endpoints.pipeline import (
    tce_analitico_indicadores,
    tce_analise_territorial,
    tce_comparisons,
    tce_contratos,
    tce_empenho_detalhe,
    tce_empenhos,
    tce_fornecedor_detalhe,
    tce_fornecedor_empenhos,
    tce_fornecedores,
    tce_kpi_portes_por_mes,
    tce_overview,
    tce_overview_municipal,
)
from app.api.router import router
from app.pipeline import analisys


configurar_logging()
logger = logging.getLogger(__name__)

__all__ = [
    "analisys",
    "app",
    "health",
    "lifespan",
    "tce_analitico_indicadores",
    "tce_analise_territorial",
    "tce_comparisons",
    "tce_contratos",
    "tce_empenho_detalhe",
    "tce_empenhos",
    "tce_fornecedor_detalhe",
    "tce_fornecedor_empenhos",
    "tce_fornecedores",
    "tce_kpi_portes_por_mes",
    "tce_overview",
    "tce_overview_municipal",
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    persistencia_logs_habilitada = False
    try:
        database.init_db()
        log_database.init_log_db()
        habilitar_persistencia_logs()
        persistencia_logs_habilitada = True
        yield
    except Exception as error:
        logger.critical(
            "Falha no ciclo de vida da API",
            extra={
                "error_type": type(error).__name__,
            },
            exc_info=True,
        )
        raise
    finally:
        if persistencia_logs_habilitada:
            desabilitar_persistencia_logs()
        database.close_pool()
        log_database.close_log_pool()
        logger.info("Recursos da API encerrados")


app = FastAPI(
    title="Monitoramento de Contas Publicas",
    description="API do modulo analitico retrospectivo de despesas empenhadas do TCE-CE.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    if usa_erro_padronizado(request.url.path):
        return resposta_erro_validacao(request, error)
    return await request_validation_exception_handler(request, error)


@app.middleware("http")
async def registrar_requisicao_http(request: Request, call_next):
    inicio = perf_counter()

    try:
        response = await call_next(request)
    except Exception as error:
        logger.exception(
            "Requisição HTTP falhou antes de gerar resposta",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration_ms": round((perf_counter() - inicio) * 1000, 2),
                "error_type": type(error).__name__,
            },
        )
        raise
    else:
        if request.url.path == "/health" and response.status_code < 400:
            nivel = logging.DEBUG
        else:
            nivel = logging.ERROR if response.status_code >= 500 else logging.INFO
        logger.log(
            nivel,
            "Requisição HTTP concluída",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - inicio) * 1000, 2),
            },
        )
        return response

app.include_router(router)

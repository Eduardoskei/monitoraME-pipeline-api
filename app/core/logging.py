from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging as stdlib_logging
import math
import os
import sys
from time import perf_counter
from typing import Any, TypeVar

from app.core import log_database


logger = stdlib_logging.getLogger(__name__)

T = TypeVar("T")

_FALHAS_EXECUCAO: ContextVar[int] = ContextVar("_FALHAS_EXECUCAO", default=0)
_ERROS_EXECUCAO: ContextVar[list[str] | None] = ContextVar("_ERROS_EXECUCAO", default=None)
_CAMPOS_PADRAO_LOG_RECORD = frozenset(
    stdlib_logging.LogRecord("", 0, "", 0, "", (), None).__dict__
)
_CHAVES_SENSIVEIS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "database_url",
    "password",
    "secret",
    "senha",
    "token",
)


def _valor_log(valor: Any) -> Any:
    """Converte campos extras para JSON sem deixar o logging quebrar a aplicação."""
    if isinstance(valor, dict):
        return {
            str(chave): "[REDACTED]"
            if any(sensivel in str(chave).lower() for sensivel in _CHAVES_SENSIVEIS)
            else _valor_log(item)
            for chave, item in valor.items()
        }
    if isinstance(valor, (list, tuple, set, frozenset)):
        return [_valor_log(item) for item in valor]
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, float) and not math.isfinite(valor):
        return str(valor)
    if valor is None or isinstance(valor, (str, int, float, bool)):
        return valor
    return str(valor)


def _contexto_record(record: stdlib_logging.LogRecord) -> dict[str, Any]:
    contexto: dict[str, Any] = {}
    for chave, valor in record.__dict__.items():
        if chave in _CAMPOS_PADRAO_LOG_RECORD or chave in {"message", "asctime"}:
            continue
        contexto[chave] = (
            "[REDACTED]"
            if any(sensivel in chave.lower() for sensivel in _CHAVES_SENSIVEIS)
            else _valor_log(valor)
        )
    return contexto


class JsonFormatter(stdlib_logging.Formatter):
    """Formato estável para ingestão por plataformas de observabilidade."""

    def format(self, record: stdlib_logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_contexto_record(record))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class DatabaseLogHandler(stdlib_logging.Handler):
    """Persiste alertas sem permitir que o banco de logs afete a aplicação."""

    RETRY_INTERVAL_SECONDS = 60.0

    def __init__(self) -> None:
        super().__init__(level=stdlib_logging.WARNING)
        self._retry_after = 0.0
        self._exception_formatter = stdlib_logging.Formatter()

    def emit(self, record: stdlib_logging.LogRecord) -> None:
        # As ingestões já possuem auditoria própria em logs_ingestao.
        if record.name == __name__:
            return

        agora = perf_counter()
        if agora < self._retry_after:
            return

        excecao = (
            self._exception_formatter.formatException(record.exc_info)
            if record.exc_info
            else None
        )
        try:
            log_database.registrar_log_aplicacao(
                nivel=record.levelname,
                logger_nome=record.name,
                mensagem=record.getMessage(),
                contexto=_contexto_record(record),
                excecao=excecao,
                criado_em=datetime.fromtimestamp(record.created, timezone.utc),
            )
        except Exception:
            self._retry_after = agora + self.RETRY_INTERVAL_SECONDS


def configurar_logging() -> None:
    """Configura os loggers da aplicação uma vez, preservando os do Uvicorn."""
    app_logger = stdlib_logging.getLogger("app")
    if any(
        getattr(handler, "_monitorame_console_handler", False)
        for handler in app_logger.handlers
    ):
        return

    nivel_nome = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    nivel = getattr(stdlib_logging, nivel_nome, stdlib_logging.INFO)
    handler = stdlib_logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._monitorame_console_handler = True  # type: ignore[attr-defined]
    app_logger.addHandler(handler)
    app_logger.setLevel(nivel)
    app_logger.propagate = False


def habilitar_persistencia_logs() -> None:
    app_logger = stdlib_logging.getLogger("app")
    if any(
        getattr(handler, "_monitorame_database_handler", False)
        for handler in app_logger.handlers
    ):
        return

    handler = DatabaseLogHandler()
    handler._monitorame_database_handler = True  # type: ignore[attr-defined]
    app_logger.addHandler(handler)


def desabilitar_persistencia_logs() -> None:
    app_logger = stdlib_logging.getLogger("app")
    handlers = [
        handler
        for handler in app_logger.handlers
        if getattr(handler, "_monitorame_database_handler", False)
    ]
    for handler in handlers:
        app_logger.removeHandler(handler)
        handler.close()


def registrar_falha_ingestao(error: Exception | str | None = None) -> None:
    erros = _ERROS_EXECUCAO.get()
    if erros is None:
        return

    _FALHAS_EXECUCAO.set(_FALHAS_EXECUCAO.get() + 1)
    if error is not None:
        erros.append(str(error))


def _quantidade_registros(resultado: Any) -> int:
    try:
        return max(0, int(len(resultado)))
    except TypeError:
        return 0


def _registrar_log_ingestao_seguro(
    *,
    fonte: str,
    etapa: str,
    parametros: dict[str, Any],
    totais: dict[str, Any],
    data_inicio: datetime,
    data_termino: datetime,
    registros_processados: int,
    falhas_ocorridas: int,
    erro: str | None,
) -> None:
    try:
        log_database.registrar_log_ingestao(
            fonte=fonte,
            etapa=etapa,
            data_inicio=data_inicio,
            data_termino=data_termino,
            registros_processados=registros_processados,
            falhas_ocorridas=falhas_ocorridas,
            parametros=parametros,
            totais={
                **totais,
                "registros_processados": registros_processados,
                "falhas_ocorridas": falhas_ocorridas,
            },
            erro=erro,
        )
    except Exception as error:
        logger.warning(
            "Falha ao persistir auditoria da ingestão",
            extra={
                "fonte": fonte,
                "etapa": etapa,
                "error_type": type(error).__name__,
            },
        )


def executar_com_log_ingestao(
    *,
    fonte: str,
    etapa: str,
    parametros: dict[str, Any],
    executar: Callable[[], T],
    totais: dict[str, Any] | None = None,
    contar_registros: Callable[[T], int] | None = None,
) -> T:
    data_inicio = datetime.now(timezone.utc)
    inicio_monotonico = perf_counter()
    token_falhas = _FALHAS_EXECUCAO.set(0)
    token_erros = _ERROS_EXECUCAO.set([])
    registros_processados = 0
    erro: str | None = None

    logger.info(
        "Ingestão iniciada",
        extra={
            "fonte": fonte,
            "etapa": etapa,
            "parametros": parametros,
        },
    )

    try:
        resultado = executar()
        registros_processados = (
            max(0, int(contar_registros(resultado)))
            if contar_registros is not None
            else _quantidade_registros(resultado)
        )
        return resultado
    except Exception as error:
        registrar_falha_ingestao(error)
        erro = str(error)
        logger.error(
            "Ingestão interrompida por erro",
            extra={
                "fonte": fonte,
                "etapa": etapa,
                "error_type": type(error).__name__,
            },
            exc_info=True,
        )
        raise
    finally:
        data_termino = datetime.now(timezone.utc)
        falhas_ocorridas = _FALHAS_EXECUCAO.get()
        erros_ocorridos = _ERROS_EXECUCAO.get() or []
        _FALHAS_EXECUCAO.reset(token_falhas)
        _ERROS_EXECUCAO.reset(token_erros)
        _registrar_log_ingestao_seguro(
            fonte=fonte,
            etapa=etapa,
            parametros=parametros,
            totais=totais or {},
            data_inicio=data_inicio,
            data_termino=data_termino,
            registros_processados=registros_processados,
            falhas_ocorridas=falhas_ocorridas,
            erro=erro or "; ".join(erros_ocorridos) or None,
        )
        nivel = stdlib_logging.WARNING if falhas_ocorridas else stdlib_logging.INFO
        logger.log(
            nivel,
            "Ingestão concluída",
            extra={
                "fonte": fonte,
                "etapa": etapa,
                "duration_ms": round((perf_counter() - inicio_monotonico) * 1000, 2),
                "registros_processados": registros_processados,
                "falhas_ocorridas": falhas_ocorridas,
            },
        )


__all__ = [
    "DatabaseLogHandler",
    "JsonFormatter",
    "configurar_logging",
    "desabilitar_persistencia_logs",
    "executar_com_log_ingestao",
    "habilitar_persistencia_logs",
    "registrar_falha_ingestao",
]

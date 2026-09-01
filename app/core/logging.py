from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime, timezone
import logging as stdlib_logging
from typing import Any, TypeVar

from app.core import log_database


logger = stdlib_logging.getLogger(__name__)

T = TypeVar("T")

_FALHAS_EXECUCAO: ContextVar[int] = ContextVar("_FALHAS_EXECUCAO", default=0)
_ERROS_EXECUCAO: ContextVar[list[str] | None] = ContextVar("_ERROS_EXECUCAO", default=None)


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
        logger.debug("Falha ao gravar log de ingestao: %s", error)


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
    token_falhas = _FALHAS_EXECUCAO.set(0)
    token_erros = _ERROS_EXECUCAO.set([])
    registros_processados = 0
    erro: str | None = None

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

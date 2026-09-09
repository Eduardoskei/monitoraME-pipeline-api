from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.core import orm
from app.core.log_models import LogIngestao


_log_schema_initialized = False


def _json_payload(valor: dict[str, Any] | None) -> dict[str, Any]:
    return json.loads(json.dumps(valor or {}, ensure_ascii=False, default=str))


def _datetime_utc(valor: datetime) -> datetime:
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


def close_log_pool() -> None:
    global _log_schema_initialized

    orm.dispose_log_engine()
    _log_schema_initialized = False


def init_log_db() -> None:
    global _log_schema_initialized

    if _log_schema_initialized:
        return

    with orm.get_log_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    _log_schema_initialized = True


def registrar_log_ingestao(
    *,
    fonte: str,
    etapa: str,
    data_inicio: datetime,
    data_termino: datetime,
    registros_processados: int,
    falhas_ocorridas: int,
    parametros: dict[str, Any] | None = None,
    totais: dict[str, Any] | None = None,
    erro: str | None = None,
) -> None:
    init_log_db()

    status = "falha" if falhas_ocorridas > 0 or erro else "sucesso"
    with orm.log_session() as session:
        session.add(
            LogIngestao(
                fonte=fonte,
                etapa=etapa,
                status=status,
                data_inicio=_datetime_utc(data_inicio),
                data_termino=_datetime_utc(data_termino),
                registros_processados=max(0, int(registros_processados)),
                falhas_ocorridas=max(0, int(falhas_ocorridas)),
                parametros=_json_payload(parametros),
                totais=_json_payload(totais),
                erro=erro,
            )
        )


__all__ = [
    "close_log_pool",
    "init_log_db",
    "registrar_log_ingestao",
]

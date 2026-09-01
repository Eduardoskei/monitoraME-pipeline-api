import json
import os
from datetime import datetime, timezone
from typing import Any

from app.core.config import LOG_DATABASE_URL

try:
    from psycopg2 import pool as pg_pool
except ImportError as error:
    pg_pool = None
    _PSYCOPG2_IMPORT_ERROR = error
else:
    _PSYCOPG2_IMPORT_ERROR = None


LOG_DATABASE_SSLMODE = os.getenv("LOG_DATABASE_SSLMODE") or os.getenv("DATABASE_SSLMODE", "require")

log_db_pool: Any | None = None
_log_schema_initialized = False


def _log_database_url() -> str:
    database_url = os.getenv("LOG_DATABASE_URL") or LOG_DATABASE_URL
    if not database_url.strip():
        raise RuntimeError("LOG_DATABASE_URL nao esta definida")

    return database_url.strip()


def _sslmode() -> str:
    return (os.getenv("LOG_DATABASE_SSLMODE") or LOG_DATABASE_SSLMODE or "require").strip()


def _ensure_driver() -> None:
    if pg_pool is None:
        raise RuntimeError(
            "psycopg2-binary nao esta instalado. Instale as dependencias antes de usar o Postgres."
        ) from _PSYCOPG2_IMPORT_ERROR


def _jsonb(valor: dict[str, Any] | None) -> str:
    return json.dumps(valor or {}, ensure_ascii=False, default=str)


def _datetime_utc(valor: datetime) -> datetime:
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


def get_log_conn() -> Any:
    global log_db_pool

    if log_db_pool is None:
        _ensure_driver()
        log_db_pool = pg_pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=_log_database_url(),
            sslmode=_sslmode(),
        )

    return log_db_pool.getconn()


def put_log_conn(conn: Any) -> None:
    if log_db_pool is not None and conn is not None:
        log_db_pool.putconn(conn)


def close_log_pool() -> None:
    global log_db_pool

    if log_db_pool is not None:
        log_db_pool.closeall()
        log_db_pool = None


def init_log_db() -> None:
    global _log_schema_initialized

    if _log_schema_initialized:
        return

    conn = get_log_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS logs_ingestao (
                    id BIGSERIAL PRIMARY KEY,
                    fonte TEXT NOT NULL,
                    etapa TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('sucesso', 'falha')),
                    data_inicio TIMESTAMPTZ NOT NULL,
                    data_termino TIMESTAMPTZ NOT NULL,
                    registros_processados INTEGER NOT NULL CHECK (registros_processados >= 0),
                    falhas_ocorridas INTEGER NOT NULL CHECK (falhas_ocorridas >= 0),
                    parametros JSONB NOT NULL DEFAULT '{}'::jsonb,
                    totais JSONB NOT NULL DEFAULT '{}'::jsonb,
                    erro TEXT,
                    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_logs_ingestao_fonte_inicio
                ON logs_ingestao (fonte, data_inicio DESC);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_logs_ingestao_etapa_status
                ON logs_ingestao (etapa, status);
                """
            )
        _log_schema_initialized = True
    finally:
        put_log_conn(conn)


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
    conn = get_log_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO logs_ingestao (
                    fonte,
                    etapa,
                    status,
                    data_inicio,
                    data_termino,
                    registros_processados,
                    falhas_ocorridas,
                    parametros,
                    totais,
                    erro
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s);
                """,
                (
                    fonte,
                    etapa,
                    status,
                    _datetime_utc(data_inicio),
                    _datetime_utc(data_termino),
                    max(0, int(registros_processados)),
                    max(0, int(falhas_ocorridas)),
                    _jsonb(parametros),
                    _jsonb(totais),
                    erro,
                ),
            )
    finally:
        put_log_conn(conn)

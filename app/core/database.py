import os
from typing import Any
from app.core.config import DATABASE_URL
from app.utils import primeiro_valor as _primeiro_valor

try:
    from psycopg2 import pool as pg_pool
except ImportError as error:
    pg_pool = None
    _PSYCOPG2_IMPORT_ERROR = error
else:
    _PSYCOPG2_IMPORT_ERROR = None


DATABASE_SSLMODE = os.getenv("DATABASE_SSLMODE", "require")

db_pool: Any | None = None
_schema_initialized = False


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL") or DATABASE_URL
    if not database_url.strip():
        raise RuntimeError("DATABASE_URL nao esta definida")

    return database_url.strip()


def _sslmode() -> str:
    return (os.getenv("DATABASE_SSLMODE") or DATABASE_SSLMODE or "require").strip()


def _ensure_driver() -> None:
    if pg_pool is None:
        raise RuntimeError(
            "psycopg2-binary nao esta instalado. Instale as dependencias antes de usar o Postgres."
        ) from _PSYCOPG2_IMPORT_ERROR


def _extrair_uf_municipio(municipio: dict[str, Any]) -> str | None:
    uf = _primeiro_valor(
        municipio,
        (
            ("uf",),
            ("UF",),
            ("sigla_uf",),
            ("microrregiao", "mesorregiao", "UF", "sigla"),
            ("regiao-imediata", "regiao-intermediaria", "UF", "sigla"),
            ("regiao_imediata", "regiao_intermediaria", "UF", "sigla"),
        ),
    )
    return str(uf) if uf not in (None, "") else None


def _montar_registro_municipio_ibge(
    municipio: dict[str, Any],
    uf: str | None = None,
) -> dict[str, Any]:
    codigo_municipio = _primeiro_valor(
        municipio,
        (
            ("id",),
            ("codigo_municipio",),
            ("codigoMunicipio",),
            ("codigoMunicipioIbge",),
        ),
    )
    nome = _primeiro_valor(municipio, (("nome",), ("nome_municipio",), ("municipio",)))
    uf_extraida = uf or _extrair_uf_municipio(municipio)

    if codigo_municipio in (None, "") or nome in (None, "") or uf_extraida in (None, ""):
        raise ValueError("Municipio do IBGE precisa conter codigo, nome e UF.")

    return {
        "codigo_municipio": str(codigo_municipio),
        "nome": str(nome),
        "uf": str(uf_extraida).upper(),
    }


def _row_to_municipio(row: tuple[Any, Any, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None

    codigo_municipio, nome, uf = row
    return {
        "id": codigo_municipio,
        "codigo_municipio": codigo_municipio,
        "nome": nome,
        "uf": uf,
    }


def _row_to_fornecedor_me(row: tuple[Any, Any, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None

    cnpj, razao_social, porte = row
    return {
        "cnpj": cnpj,
        "razao_social": razao_social,
        "porte": porte,
    }


def get_conn() -> Any:
    global db_pool

    if db_pool is None:
        _ensure_driver()
        db_pool = pg_pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=_database_url(),
            sslmode=_sslmode(),
        )

    return db_pool.getconn()


def put_conn(conn: Any) -> None:
    if db_pool is not None and conn is not None:
        db_pool.putconn(conn)


def close_pool() -> None:
    global db_pool

    if db_pool is not None:
        db_pool.closeall()
        db_pool = None


def init_db() -> None:
    global _schema_initialized

    if _schema_initialized:
        return

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                DROP TABLE IF EXISTS ibge_cache;
                """
            )
            cur.execute(
                """
                DROP TABLE IF EXISTS municipio_coordenadas;
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ibge_municipios (
                    codigo_municipio TEXT PRIMARY KEY,
                    nome TEXT NOT NULL,
                    uf TEXT NOT NULL
                );
                """
            )
            cur.execute(
                """
                ALTER TABLE ibge_municipios
                    DROP COLUMN IF EXISTS nome_normalizado,
                    DROP COLUMN IF EXISTS uf_normalizada,
                    DROP COLUMN IF EXISTS microrregiao,
                    DROP COLUMN IF EXISTS mesorregiao,
                    DROP COLUMN IF EXISTS regiao_imediata,
                    DROP COLUMN IF EXISTS regiao_intermediaria,
                    DROP COLUMN IF EXISTS payload,
                    DROP COLUMN IF EXISTS updated_at;
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ibge_municipios_uf_nome
                ON ibge_municipios (uf, nome);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fornecedores_me (
                    cnpj TEXT PRIMARY KEY,
                    razao_social TEXT,
                    porte TEXT NOT NULL CHECK (porte = 'ME')
                );
                """
            )
        _schema_initialized = True
    finally:
        put_conn(conn)


_UPSERT_MUNICIPIO_SQL = """
    INSERT INTO ibge_municipios (
        codigo_municipio,
        nome,
        uf
    )
    VALUES (%s, %s, %s)
    ON CONFLICT (codigo_municipio)
    DO UPDATE SET
        nome = EXCLUDED.nome,
        uf = EXCLUDED.uf;
"""


def _execute_upsert_municipio(cur: Any, registro: dict[str, Any]) -> None:
    cur.execute(
        _UPSERT_MUNICIPIO_SQL,
        (
            registro["codigo_municipio"],
            registro["nome"],
            registro["uf"],
        ),
    )


def salvar_municipio_ibge(municipio: dict[str, Any], uf: str | None = None) -> None:
    init_db()
    registro = _montar_registro_municipio_ibge(municipio, uf)

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            _execute_upsert_municipio(cur, registro)
    finally:
        put_conn(conn)


def salvar_municipios_ibge(municipios: list[dict[str, Any]], uf: str | None = None) -> int:
    init_db()
    registros: list[dict[str, Any]] = []
    for municipio in municipios:
        try:
            registros.append(_montar_registro_municipio_ibge(municipio, uf))
        except ValueError:
            continue

    if not registros:
        return 0

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            for registro in registros:
                _execute_upsert_municipio(cur, registro)
    finally:
        put_conn(conn)

    return len(registros)


def listar_municipios_ibge(uf: str | None = None) -> list[dict[str, Any]]:
    init_db()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if uf:
                cur.execute(
                    """
                    SELECT codigo_municipio, nome, uf
                    FROM ibge_municipios
                    WHERE uf = %s
                    ORDER BY nome;
                    """,
                    (uf.upper(),),
                )
            else:
                cur.execute(
                    """
                    SELECT codigo_municipio, nome, uf
                    FROM ibge_municipios
                    ORDER BY uf, nome;
                    """
                )
            return [
                municipio
                for row in cur.fetchall()
                if (municipio := _row_to_municipio(row)) is not None
            ]
    finally:
        put_conn(conn)


def localizar_municipio_ibge(codigo_municipio: str | int) -> dict[str, Any] | None:
    init_db()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT codigo_municipio, nome, uf
                FROM ibge_municipios
                WHERE codigo_municipio = %s
                """,
                (str(codigo_municipio),),
            )
            return _row_to_municipio(cur.fetchone())
    finally:
        put_conn(conn)


def localizar_municipio_por_nome_ibge(nome_municipio: str, uf: str) -> dict[str, Any] | None:
    init_db()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT codigo_municipio, nome, uf
                FROM ibge_municipios
                WHERE LOWER(nome) = LOWER(%s) AND uf = %s
                """,
                (nome_municipio, uf.upper()),
            )
            return _row_to_municipio(cur.fetchone())
    finally:
        put_conn(conn)


def salvar_fornecedor_me(cnpj: str, razao_social: str | None, porte: str = "ME") -> None:
    if porte != "ME":
        raise ValueError("Apenas fornecedores ME devem ser salvos.")

    init_db()
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fornecedores_me (cnpj, razao_social, porte)
                VALUES (%s, %s, %s)
                ON CONFLICT (cnpj)
                DO UPDATE SET
                    razao_social = EXCLUDED.razao_social,
                    porte = EXCLUDED.porte;
                """,
                (cnpj, razao_social, porte),
            )
    finally:
        put_conn(conn)


def localizar_fornecedor_me(cnpj: str) -> dict[str, Any] | None:
    init_db()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cnpj, razao_social, porte
                FROM fornecedores_me
                WHERE cnpj = %s
                """,
                (cnpj,),
            )
            return _row_to_fornecedor_me(cur.fetchone())
    finally:
        put_conn(conn)

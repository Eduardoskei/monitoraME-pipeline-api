from datetime import datetime, timezone
import json
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


def _jsonb(valor: dict[str, Any] | None) -> str:
    return json.dumps(valor or {}, ensure_ascii=False, default=str)


def _datetime_utc(valor: datetime) -> datetime:
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pncp_ingestion_state (
                    escopo TEXT PRIMARY KEY,
                    ultima_execucao_sucesso TIMESTAMPTZ NOT NULL,
                    parametros JSONB NOT NULL DEFAULT '{}'::jsonb,
                    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pncp_ingestion_runs (
                    id BIGSERIAL PRIMARY KEY,
                    escopo TEXT NOT NULL,
                    status_execucao TEXT NOT NULL CHECK (status_execucao IN ('sucesso', 'falha')),
                    executado_em TIMESTAMPTZ NOT NULL,
                    janela_inicio TIMESTAMPTZ NOT NULL,
                    janela_fim TIMESTAMPTZ NOT NULL,
                    quantidade_lida INTEGER NOT NULL CHECK (quantidade_lida >= 0),
                    quantidade_inserida INTEGER NOT NULL CHECK (quantidade_inserida >= 0),
                    quantidade_atualizada INTEGER NOT NULL CHECK (quantidade_atualizada >= 0),
                    quantidade_com_erro INTEGER NOT NULL CHECK (quantidade_com_erro >= 0),
                    parametros JSONB NOT NULL DEFAULT '{}'::jsonb,
                    totais JSONB NOT NULL DEFAULT '{}'::jsonb,
                    erro TEXT,
                    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pncp_ingestion_runs_escopo_execucao
                ON pncp_ingestion_runs (escopo, executado_em DESC);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pncp_contratacoes (
                    numero_controle_pncp TEXT PRIMARY KEY,
                    cnpj_orgao TEXT,
                    ano_compra INTEGER,
                    sequencial_compra INTEGER,
                    modalidade_id INTEGER,
                    modalidade_nome TEXT,
                    objeto_compra TEXT,
                    natureza_despesa_monitorada TEXT,
                    data_publicacao_pncp TIMESTAMPTZ,
                    data_atualizacao TIMESTAMPTZ,
                    data_atualizacao_global TIMESTAMPTZ,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    inserido_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pncp_contratacoes_datas
                ON pncp_contratacoes (data_publicacao_pncp, data_atualizacao, data_atualizacao_global);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pncp_contratacoes_modalidade_uf
                ON pncp_contratacoes (modalidade_id, cnpj_orgao);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pncp_itens (
                    item_id TEXT PRIMARY KEY,
                    numero_controle_pncp TEXT NOT NULL REFERENCES pncp_contratacoes(numero_controle_pncp) ON DELETE CASCADE,
                    numero_item INTEGER,
                    descricao TEXT,
                    material_ou_servico_nome TEXT,
                    item_categoria_id INTEGER,
                    item_categoria_nome TEXT,
                    categoria_item_catalogo_id INTEGER,
                    categoria_item_catalogo_nome TEXT,
                    classificacao_superior_codigo TEXT,
                    classificacao_superior_nome TEXT,
                    ncm_nbs_codigo TEXT,
                    ncm_nbs_descricao TEXT,
                    valor_total NUMERIC,
                    natureza_despesa_monitorada TEXT,
                    data_inclusao TIMESTAMPTZ,
                    data_atualizacao TIMESTAMPTZ,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    inserido_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pncp_itens_contratacao
                ON pncp_itens (numero_controle_pncp);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pncp_resultados (
                    resultado_id TEXT PRIMARY KEY,
                    numero_controle_pncp TEXT NOT NULL REFERENCES pncp_contratacoes(numero_controle_pncp) ON DELETE CASCADE,
                    numero_item INTEGER,
                    ni_fornecedor TEXT,
                    nome_razao_social_fornecedor TEXT,
                    porte_fornecedor_id INTEGER,
                    porte_fornecedor_nome TEXT,
                    porte_fornecedor_padronizado TEXT,
                    valor_total_homologado NUMERIC,
                    data_resultado TIMESTAMPTZ,
                    data_cancelamento TIMESTAMPTZ,
                    data_inclusao TIMESTAMPTZ,
                    data_atualizacao TIMESTAMPTZ,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    inserido_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pncp_resultados_fornecedor
                ON pncp_resultados (ni_fornecedor);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pncp_contratos (
                    contrato_id TEXT PRIMARY KEY,
                    numero_controle_pncp TEXT NOT NULL REFERENCES pncp_contratacoes(numero_controle_pncp) ON DELETE CASCADE,
                    ano_contrato INTEGER,
                    sequencial_contrato INTEGER,
                    numero_contrato_empenho TEXT,
                    ni_fornecedor TEXT,
                    nome_razao_social_fornecedor TEXT,
                    valor_global NUMERIC,
                    data_assinatura TIMESTAMPTZ,
                    data_vigencia_inicio TIMESTAMPTZ,
                    data_vigencia_fim TIMESTAMPTZ,
                    data_publicacao_pncp TIMESTAMPTZ,
                    data_atualizacao TIMESTAMPTZ,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    inserido_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pncp_contratos_fornecedor
                ON pncp_contratos (ni_fornecedor);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pncp_pca_planos (
                    pca_id TEXT PRIMARY KEY,
                    numero_controle_pncp TEXT,
                    cnpj_orgao TEXT,
                    ano_pca INTEGER,
                    sequencial_pca INTEGER,
                    codigo_unidade TEXT,
                    nome_unidade TEXT,
                    municipio TEXT,
                    uf TEXT,
                    natureza_despesa_monitorada TEXT,
                    data_publicacao_pncp TIMESTAMPTZ,
                    data_atualizacao TIMESTAMPTZ,
                    data_atualizacao_global TIMESTAMPTZ,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    inserido_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pncp_pca_planos_orgao_ano
                ON pncp_pca_planos (cnpj_orgao, ano_pca);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pncp_pca_itens (
                    pca_item_id TEXT PRIMARY KEY,
                    pca_id TEXT NOT NULL REFERENCES pncp_pca_planos(pca_id) ON DELETE CASCADE,
                    numero_controle_pncp TEXT,
                    cnpj_orgao TEXT,
                    ano_pca INTEGER,
                    sequencial_pca INTEGER,
                    numero_item INTEGER,
                    categoria_item_pca_id INTEGER,
                    categoria_item_pca_nome TEXT,
                    classificacao_superior_codigo TEXT,
                    classificacao_superior_nome TEXT,
                    pdm_codigo TEXT,
                    pdm_descricao TEXT,
                    codigo_item TEXT,
                    descricao TEXT,
                    unidade_fornecimento TEXT,
                    quantidade NUMERIC,
                    valor_unitario NUMERIC,
                    valor_total NUMERIC,
                    valor_orcamento_exercicio NUMERIC,
                    natureza_despesa_monitorada TEXT,
                    data_desejada TIMESTAMPTZ,
                    data_publicacao_pncp TIMESTAMPTZ,
                    data_inclusao TIMESTAMPTZ,
                    data_atualizacao TIMESTAMPTZ,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    inserido_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pncp_pca_itens_pca
                ON pncp_pca_itens (pca_id);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pncp_fornecedores (
                    cnpj TEXT PRIMARY KEY,
                    razao_social TEXT,
                    porte TEXT,
                    porte_padronizado TEXT,
                    municipio_sede TEXT,
                    uf_sede TEXT,
                    cnae_principal_codigo TEXT,
                    cnae_principal_descricao TEXT,
                    elegivel_me BOOLEAN,
                    cnpj_valido BOOLEAN,
                    opencnpj_status TEXT,
                    optante_simples_nacional BOOLEAN,
                    optante_mei BOOLEAN,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    inserido_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
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


def buscar_checkpoint_pncp(escopo: str) -> datetime | None:
    init_db()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ultima_execucao_sucesso
                FROM pncp_ingestion_state
                WHERE escopo = %s;
                """,
                (escopo,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        put_conn(conn)


def salvar_checkpoint_pncp(
    *,
    escopo: str,
    ultima_execucao_sucesso: datetime,
    parametros: dict[str, Any] | None = None,
) -> None:
    init_db()
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            _execute_salvar_checkpoint_pncp(
                cur,
                escopo=escopo,
                ultima_execucao_sucesso=ultima_execucao_sucesso,
                parametros=parametros,
            )
    finally:
        put_conn(conn)


def _execute_salvar_checkpoint_pncp(
    cur: Any,
    *,
    escopo: str,
    ultima_execucao_sucesso: datetime,
    parametros: dict[str, Any] | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO pncp_ingestion_state (
            escopo,
            ultima_execucao_sucesso,
            parametros,
            atualizado_em
        )
        VALUES (%s, %s, %s::jsonb, NOW())
        ON CONFLICT (escopo)
        DO UPDATE SET
            ultima_execucao_sucesso = EXCLUDED.ultima_execucao_sucesso,
            parametros = EXCLUDED.parametros,
            atualizado_em = NOW();
        """,
        (
            escopo,
            _datetime_utc(ultima_execucao_sucesso),
            _jsonb(parametros),
        ),
    )


def registrar_execucao_pncp(
    *,
    escopo: str,
    status_execucao: str,
    executado_em: datetime,
    janela_inicio: datetime,
    janela_fim: datetime,
    quantidade_lida: int,
    quantidade_inserida: int,
    quantidade_atualizada: int,
    quantidade_com_erro: int,
    parametros: dict[str, Any] | None = None,
    totais: dict[str, Any] | None = None,
    erro: str | None = None,
) -> None:
    init_db()
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            _execute_registrar_execucao_pncp(
                cur,
                escopo=escopo,
                status_execucao=status_execucao,
                executado_em=executado_em,
                janela_inicio=janela_inicio,
                janela_fim=janela_fim,
                quantidade_lida=quantidade_lida,
                quantidade_inserida=quantidade_inserida,
                quantidade_atualizada=quantidade_atualizada,
                quantidade_com_erro=quantidade_com_erro,
                parametros=parametros,
                totais=totais,
                erro=erro,
            )
    finally:
        put_conn(conn)


def _execute_registrar_execucao_pncp(
    cur: Any,
    *,
    escopo: str,
    status_execucao: str,
    executado_em: datetime,
    janela_inicio: datetime,
    janela_fim: datetime,
    quantidade_lida: int,
    quantidade_inserida: int,
    quantidade_atualizada: int,
    quantidade_com_erro: int,
    parametros: dict[str, Any] | None = None,
    totais: dict[str, Any] | None = None,
    erro: str | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO pncp_ingestion_runs (
            escopo,
            status_execucao,
            executado_em,
            janela_inicio,
            janela_fim,
            quantidade_lida,
            quantidade_inserida,
            quantidade_atualizada,
            quantidade_com_erro,
            parametros,
            totais,
            erro
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s);
        """,
        (
            escopo,
            status_execucao,
            _datetime_utc(executado_em),
            _datetime_utc(janela_inicio),
            _datetime_utc(janela_fim),
            max(0, int(quantidade_lida)),
            max(0, int(quantidade_inserida)),
            max(0, int(quantidade_atualizada)),
            max(0, int(quantidade_com_erro)),
            _jsonb(parametros),
            _jsonb(totais),
            erro,
        ),
    )


def registrar_sucesso_e_checkpoint_pncp(
    *,
    escopo: str,
    executado_em: datetime,
    janela_inicio: datetime,
    janela_fim: datetime,
    quantidade_lida: int,
    quantidade_inserida: int,
    quantidade_atualizada: int,
    parametros: dict[str, Any] | None = None,
    totais: dict[str, Any] | None = None,
) -> None:
    init_db()
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            _execute_registrar_execucao_pncp(
                cur,
                escopo=escopo,
                status_execucao="sucesso",
                executado_em=executado_em,
                janela_inicio=janela_inicio,
                janela_fim=janela_fim,
                quantidade_lida=quantidade_lida,
                quantidade_inserida=quantidade_inserida,
                quantidade_atualizada=quantidade_atualizada,
                quantidade_com_erro=0,
                parametros=parametros,
                totais=totais,
            )
            _execute_salvar_checkpoint_pncp(
                cur,
                escopo=escopo,
                ultima_execucao_sucesso=janela_fim,
                parametros=parametros,
            )
    finally:
        put_conn(conn)

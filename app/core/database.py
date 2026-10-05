from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.core import orm
from app.core.models import (
    FornecedorCache,
    FornecedorMe,
    IbgeMunicipio,
    TceMunicipio,
)
from app.utils import primeiro_valor as _primeiro_valor


_schema_initialized = False


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


def _municipio_to_dict(municipio: IbgeMunicipio | None) -> dict[str, Any] | None:
    if municipio is None:
        return None

    return {
        "id": municipio.codigo_municipio,
        "codigo_municipio": municipio.codigo_municipio,
        "nome": municipio.nome,
        "uf": municipio.uf,
    }


def _fornecedor_me_to_dict(fornecedor: FornecedorMe | None) -> dict[str, Any] | None:
    if fornecedor is None:
        return None

    return {
        "cnpj": fornecedor.cnpj,
        "razao_social": fornecedor.razao_social,
        "porte": fornecedor.porte,
    }


def _fornecedor_cache_to_dict(fornecedor: FornecedorCache) -> dict[str, Any]:
    return {
        "cnpj": fornecedor.cnpj,
        "opencnpj_status": fornecedor.opencnpj_status,
        "dados_normalizados": fornecedor.dados_normalizados,
        "payload": fornecedor.payload,
        "observado_em": fornecedor.observado_em,
        "expira_em": fornecedor.expira_em,
        "ultima_tentativa_em": fornecedor.ultima_tentativa_em,
        "ultima_tentativa_status": fornecedor.ultima_tentativa_status,
        "cache_desatualizado": fornecedor.cache_desatualizado,
        "atualizado_em": fornecedor.atualizado_em,
    }


def _montar_registro_municipio_tce(
    municipio: dict[str, Any],
    uf: str,
) -> dict[str, str]:
    codigo_tce = municipio.get("codigo_municipio")
    codigo_ibge = municipio.get("codigo_municipio_ibge")
    nome = municipio.get("nome_municipio") or municipio.get("nome")

    if codigo_tce in (None, "") or codigo_ibge in (None, "") or nome in (None, ""):
        raise ValueError(
            "Municipio do TCE precisa conter codigo_municipio, "
            "codigo_municipio_ibge e nome_municipio."
        )

    return {
        "codigo_municipio_tce": str(codigo_tce).strip(),
        "codigo_municipio_ibge": str(codigo_ibge).strip(),
        "nome": str(nome).strip(),
        "uf": str(uf).strip().upper(),
    }


def close_pool() -> None:
    global _schema_initialized

    orm.dispose_main_engine()
    _schema_initialized = False


def init_db() -> None:
    global _schema_initialized

    if _schema_initialized:
        return

    with orm.get_main_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    _schema_initialized = True


def salvar_municipio_ibge(municipio: dict[str, Any], uf: str | None = None) -> None:
    init_db()
    registro = _montar_registro_municipio_ibge(municipio, uf)

    with orm.main_session() as session:
        session.merge(IbgeMunicipio(**registro))


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

    with orm.main_session() as session:
        for registro in registros:
            session.merge(IbgeMunicipio(**registro))

    return len(registros)


def listar_municipios_ibge(uf: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with orm.main_session() as session:
        stmt = select(IbgeMunicipio)
        if uf:
            stmt = stmt.where(IbgeMunicipio.uf == uf.upper()).order_by(IbgeMunicipio.nome)
        else:
            stmt = stmt.order_by(IbgeMunicipio.uf, IbgeMunicipio.nome)

        return [
            municipio_dict
            for municipio in session.scalars(stmt).all()
            if (municipio_dict := _municipio_to_dict(municipio)) is not None
        ]


def localizar_municipio_ibge(codigo_municipio: str | int) -> dict[str, Any] | None:
    init_db()
    with orm.main_session() as session:
        return _municipio_to_dict(session.get(IbgeMunicipio, str(codigo_municipio)))


def localizar_municipio_por_nome_ibge(nome_municipio: str, uf: str) -> dict[str, Any] | None:
    init_db()
    with orm.main_session() as session:
        municipio = session.scalars(
            select(IbgeMunicipio).where(
                func.lower(IbgeMunicipio.nome) == func.lower(nome_municipio),
                IbgeMunicipio.uf == uf.upper(),
            )
        ).first()
        return _municipio_to_dict(municipio)


def salvar_municipios_tce(municipios: list[dict[str, Any]], uf: str) -> int:
    """Persiste em lote o mapa TCE -> IBGE e os nomes usados na consulta local."""
    init_db()
    registros_por_codigo: dict[str, dict[str, str]] = {}
    for municipio in municipios:
        try:
            registro = _montar_registro_municipio_tce(municipio, uf)
        except ValueError:
            continue
        registros_por_codigo[registro["codigo_municipio_tce"]] = registro

    registros = list(registros_por_codigo.values())
    if not registros:
        return 0

    municipios_ibge = [
        {
            "codigo_municipio": registro["codigo_municipio_ibge"],
            "nome": registro["nome"],
            "uf": registro["uf"],
        }
        for registro in registros
    ]
    mapeamentos_tce = [
        {
            "codigo_municipio_tce": registro["codigo_municipio_tce"],
            "codigo_municipio_ibge": registro["codigo_municipio_ibge"],
        }
        for registro in registros
    ]

    inserir_ibge = insert(IbgeMunicipio)
    inserir_ibge_se_ausente = inserir_ibge.on_conflict_do_nothing(
        index_elements=[IbgeMunicipio.codigo_municipio],
    )
    inserir_tce = insert(TceMunicipio)
    atualizar_tce = inserir_tce.on_conflict_do_update(
        index_elements=[TceMunicipio.codigo_municipio_tce],
        set_={
            "codigo_municipio_ibge": inserir_tce.excluded.codigo_municipio_ibge,
        },
    )

    with orm.main_session() as session:
        # Se o IBGE ja foi sincronizado, preserva seu nome oficial. O nome
        # recebido do TCE serve apenas para preencher municipios ainda ausentes.
        session.execute(inserir_ibge_se_ausente, municipios_ibge)
        session.execute(atualizar_tce, mapeamentos_tce)

    return len(registros)


def localizar_municipio_tce(codigo_municipio_tce: str | int) -> dict[str, str] | None:
    """Resolve um codigo interno do TCE usando apenas as tabelas locais."""
    init_db()
    consulta = (
        select(
            TceMunicipio.codigo_municipio_tce,
            TceMunicipio.codigo_municipio_ibge,
            IbgeMunicipio.nome,
            IbgeMunicipio.uf,
        )
        .join(
            IbgeMunicipio,
            IbgeMunicipio.codigo_municipio == TceMunicipio.codigo_municipio_ibge,
        )
        .where(TceMunicipio.codigo_municipio_tce == str(codigo_municipio_tce).strip())
    )
    with orm.main_session() as session:
        registro = session.execute(consulta).mappings().first()

    return dict(registro) if registro is not None else None


def localizar_municipio_tce_por_ibge(
    codigo_municipio_ibge: str | int,
    *,
    uf: str | None = None,
) -> dict[str, str] | None:
    """Resolve o municipio e o codigo interno do TCE usando apenas o banco local."""
    init_db()
    consulta = (
        select(
            TceMunicipio.codigo_municipio_tce,
            TceMunicipio.codigo_municipio_ibge,
            IbgeMunicipio.nome,
            IbgeMunicipio.uf,
        )
        .join(
            IbgeMunicipio,
            IbgeMunicipio.codigo_municipio == TceMunicipio.codigo_municipio_ibge,
        )
        .where(
            TceMunicipio.codigo_municipio_ibge == str(codigo_municipio_ibge).strip()
        )
    )
    if uf:
        consulta = consulta.where(IbgeMunicipio.uf == str(uf).strip().upper())

    with orm.main_session() as session:
        registro = session.execute(consulta).mappings().first()

    return dict(registro) if registro is not None else None


def salvar_fornecedor_me(cnpj: str, razao_social: str | None, porte: str = "ME") -> None:
    if porte != "ME":
        raise ValueError("Apenas fornecedores ME devem ser salvos.")

    init_db()
    with orm.main_session() as session:
        session.merge(FornecedorMe(cnpj=cnpj, razao_social=razao_social, porte=porte))


def listar_fornecedores_cache(cnpjs: list[str]) -> dict[str, dict[str, Any]]:
    init_db()
    cnpjs_unicos = tuple(dict.fromkeys(str(cnpj).strip() for cnpj in cnpjs if str(cnpj).strip()))
    if not cnpjs_unicos:
        return {}

    consulta = select(FornecedorCache).where(FornecedorCache.cnpj.in_(cnpjs_unicos))
    with orm.main_session() as session:
        fornecedores = session.scalars(consulta).all()
    return {fornecedor.cnpj: _fornecedor_cache_to_dict(fornecedor) for fornecedor in fornecedores}


def salvar_fornecedores_cache(registros: list[dict[str, Any]]) -> int:
    init_db()
    if not registros:
        return 0

    inserir = insert(FornecedorCache)
    atualizar = inserir.on_conflict_do_update(
        index_elements=[FornecedorCache.cnpj],
        set_={
            "opencnpj_status": inserir.excluded.opencnpj_status,
            "dados_normalizados": inserir.excluded.dados_normalizados,
            "payload": inserir.excluded.payload,
            "observado_em": inserir.excluded.observado_em,
            "expira_em": inserir.excluded.expira_em,
            "ultima_tentativa_em": inserir.excluded.ultima_tentativa_em,
            "ultima_tentativa_status": inserir.excluded.ultima_tentativa_status,
            "cache_desatualizado": inserir.excluded.cache_desatualizado,
            "atualizado_em": inserir.excluded.atualizado_em,
        },
    )
    with orm.main_session() as session:
        session.execute(atualizar, registros)
    return len(registros)


def localizar_fornecedor_me(cnpj: str) -> dict[str, Any] | None:
    init_db()
    with orm.main_session() as session:
        return _fornecedor_me_to_dict(session.get(FornecedorMe, cnpj))


__all__ = [
    "close_pool",
    "init_db",
    "listar_fornecedores_cache",
    "listar_municipios_ibge",
    "localizar_fornecedor_me",
    "localizar_municipio_ibge",
    "localizar_municipio_por_nome_ibge",
    "localizar_municipio_tce",
    "localizar_municipio_tce_por_ibge",
    "salvar_fornecedor_me",
    "salvar_fornecedores_cache",
    "salvar_municipio_ibge",
    "salvar_municipios_ibge",
    "salvar_municipios_tce",
]

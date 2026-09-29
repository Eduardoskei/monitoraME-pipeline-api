from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text

from app.core import orm
from app.core.models import (
    FornecedorMe,
    IbgeMunicipio,
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


def salvar_fornecedor_me(cnpj: str, razao_social: str | None, porte: str = "ME") -> None:
    if porte != "ME":
        raise ValueError("Apenas fornecedores ME devem ser salvos.")

    init_db()
    with orm.main_session() as session:
        session.merge(FornecedorMe(cnpj=cnpj, razao_social=razao_social, porte=porte))


def localizar_fornecedor_me(cnpj: str) -> dict[str, Any] | None:
    init_db()
    with orm.main_session() as session:
        return _fornecedor_me_to_dict(session.get(FornecedorMe, cnpj))


__all__ = [
    "close_pool",
    "init_db",
    "listar_municipios_ibge",
    "localizar_fornecedor_me",
    "localizar_municipio_ibge",
    "localizar_municipio_por_nome_ibge",
    "salvar_fornecedor_me",
    "salvar_municipio_ibge",
    "salvar_municipios_ibge",
]

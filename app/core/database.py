from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import orm
from app.core.models import (
    FornecedorMe,
    IbgeMunicipio,
    PncpIngestionRun,
    PncpIngestionState,
)
from app.utils import primeiro_valor as _primeiro_valor


_schema_initialized = False


class RawConnectionAdapter:
    def __init__(self, raw_connection: Any) -> None:
        self._raw_connection = raw_connection

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw_connection, name)

    def __enter__(self) -> "RawConnectionAdapter":
        return self

    def __exit__(self, exc_type: object, _exc: object, _tb: object) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()

    def cursor(self) -> Any:
        return self._raw_connection.cursor()

    def commit(self) -> None:
        self._raw_connection.commit()

    def rollback(self) -> None:
        self._raw_connection.rollback()

    def close(self) -> None:
        self._raw_connection.close()


def _jsonb(valor: dict[str, Any] | None) -> str:
    return json.dumps(valor or {}, ensure_ascii=False, default=str)


def _json_payload(valor: dict[str, Any] | None) -> dict[str, Any]:
    return json.loads(_jsonb(valor))


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


def get_conn() -> RawConnectionAdapter:
    return RawConnectionAdapter(orm.get_main_engine().raw_connection())


def put_conn(conn: Any) -> None:
    if conn is not None:
        conn.close()


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


def buscar_checkpoint_pncp(escopo: str) -> datetime | None:
    init_db()
    with orm.main_session() as session:
        estado = session.get(PncpIngestionState, escopo)
        return estado.ultima_execucao_sucesso if estado is not None else None


def salvar_checkpoint_pncp(
    *,
    escopo: str,
    ultima_execucao_sucesso: datetime,
    parametros: dict[str, Any] | None = None,
) -> None:
    init_db()
    with orm.main_session() as session:
        _execute_salvar_checkpoint_pncp(
            session,
            escopo=escopo,
            ultima_execucao_sucesso=ultima_execucao_sucesso,
            parametros=parametros,
        )


def _execute_salvar_checkpoint_pncp(
    session: Session,
    *,
    escopo: str,
    ultima_execucao_sucesso: datetime,
    parametros: dict[str, Any] | None = None,
) -> None:
    session.merge(
        PncpIngestionState(
            escopo=escopo,
            ultima_execucao_sucesso=_datetime_utc(ultima_execucao_sucesso),
            parametros=_json_payload(parametros),
            atualizado_em=datetime.now(timezone.utc),
        )
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
    with orm.main_session() as session:
        _execute_registrar_execucao_pncp(
            session,
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


def _execute_registrar_execucao_pncp(
    session: Session,
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
    session.add(
        PncpIngestionRun(
            escopo=escopo,
            status_execucao=status_execucao,
            executado_em=_datetime_utc(executado_em),
            janela_inicio=_datetime_utc(janela_inicio),
            janela_fim=_datetime_utc(janela_fim),
            quantidade_lida=max(0, int(quantidade_lida)),
            quantidade_inserida=max(0, int(quantidade_inserida)),
            quantidade_atualizada=max(0, int(quantidade_atualizada)),
            quantidade_com_erro=max(0, int(quantidade_com_erro)),
            parametros=_json_payload(parametros),
            totais=_json_payload(totais),
            erro=erro,
        )
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
    with orm.main_session() as session:
        _execute_registrar_execucao_pncp(
            session,
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
            session,
            escopo=escopo,
            ultima_execucao_sucesso=janela_fim,
            parametros=parametros,
        )


__all__ = [
    "RawConnectionAdapter",
    "buscar_checkpoint_pncp",
    "close_pool",
    "get_conn",
    "init_db",
    "listar_municipios_ibge",
    "localizar_fornecedor_me",
    "localizar_municipio_ibge",
    "localizar_municipio_por_nome_ibge",
    "put_conn",
    "registrar_execucao_pncp",
    "registrar_sucesso_e_checkpoint_pncp",
    "salvar_checkpoint_pncp",
    "salvar_fornecedor_me",
    "salvar_municipio_ibge",
    "salvar_municipios_ibge",
]

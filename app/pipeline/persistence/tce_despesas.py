"""Persistência versionada e publicação atômica das despesas do TCE-CE."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from typing import Any, Iterable

from sqlalchemy import case, delete, func, or_, select, text
from sqlalchemy.orm import Session

from app.core import database, orm
from app.core.models import (
    FornecedorCache,
    IbgeMunicipio,
    TceAnulacaoEmpenho,
    TceDespesaIngestionRun,
    TceEmpenho,
    TceMunicipio,
)


STATUS_EM_VALIDACAO = "EM_VALIDACAO"
STATUS_PUBLICADO = "PUBLICADO"
STATUS_SUBSTITUIDO = "SUBSTITUIDO"
STATUS_REJEITADO = "REJEITADO"
RETENCAO_LOTES_SUBSTITUIDOS = timedelta(days=7)


@dataclass(frozen=True)
class LoteTceValidado:
    empenhos: list[dict[str, Any]]
    anulacoes: list[dict[str, Any]]
    problemas: list[dict[str, Any]]

    @property
    def valido(self) -> bool:
        return not self.problemas


@dataclass(frozen=True)
class ResultadoPublicacaoTce:
    run_id: int
    status: str
    quantidade_empenhos: int
    quantidade_anulacoes: int
    problemas: list[dict[str, Any]]
    run_anterior_id: int | None = None


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _payload_json(valor: Any) -> Any:
    return json.loads(json.dumps(valor, ensure_ascii=False, default=str))


def _problema(codigo: str, mensagem: str, **contexto: Any) -> dict[str, Any]:
    return {"codigo": codigo, "mensagem": mensagem, **contexto}


def _deduplicar(
    registros: Iterable[dict[str, Any]],
    *,
    campo_chave: str,
    tipo: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unicos: dict[str, dict[str, Any]] = {}
    problemas: list[dict[str, Any]] = []
    for registro in registros:
        chave = str(registro.get(campo_chave) or "").strip()
        if not chave:
            problemas.append(_problema("CHAVE_AUSENTE", f"{tipo} sem {campo_chave}."))
            continue
        anterior = unicos.get(chave)
        if anterior is None:
            unicos[chave] = registro
        elif anterior != registro:
            problemas.append(
                _problema(
                    "DUPLICIDADE_CONFLITANTE",
                    f"{tipo} repetido com conteúdos diferentes.",
                    chave=chave,
                )
            )
    return list(unicos.values()), problemas


def validar_lote_tce(
    *,
    codigo_municipio_tce: str,
    competencia: str,
    empenhos: Iterable[dict[str, Any]],
    anulacoes: Iterable[dict[str, Any]],
    valores_empenhos_publicados: dict[str, int] | None = None,
    anulacoes_publicadas_centavos: dict[str, int] | None = None,
) -> LoteTceValidado:
    """Deduplica e valida o lote contra os demais lotes atualmente publicados."""
    empenhos_unicos, problemas_empenhos = _deduplicar(
        empenhos,
        campo_chave="chave_empenho",
        tipo="Empenho",
    )
    anulacoes_unicas, problemas_anulacoes = _deduplicar(
        anulacoes,
        campo_chave="chave_anulacao",
        tipo="Anulação",
    )
    problemas = [*problemas_empenhos, *problemas_anulacoes]

    for tipo, registros in (("Empenho", empenhos_unicos), ("Anulação", anulacoes_unicas)):
        for registro in registros:
            if registro.get("codigo_municipio_tce") != codigo_municipio_tce:
                problemas.append(
                    _problema(
                        "MUNICIPIO_DIVERGENTE",
                        f"{tipo} pertence a outro município.",
                        chave=registro.get("chave_empenho"),
                    )
                )
            if registro.get("competencia") != competencia:
                problemas.append(
                    _problema(
                        "COMPETENCIA_DIVERGENTE",
                        f"{tipo} pertence a outra competência.",
                        chave=registro.get("chave_empenho"),
                    )
                )

    valores_empenhos = dict(valores_empenhos_publicados or {})
    for empenho in empenhos_unicos:
        chave = str(empenho["chave_empenho"])
        valor = int(empenho["valor_empenhado_centavos"])
        if valor < 0:
            problemas.append(
                _problema(
                    "VALOR_EMPENHO_NEGATIVO",
                    "Empenho possui valor negativo.",
                    chave=chave,
                    valor_centavos=valor,
                )
            )
        valores_empenhos[chave] = valor

    totais_anulacoes: defaultdict[str, int] = defaultdict(int)
    for chave, valor in (anulacoes_publicadas_centavos or {}).items():
        totais_anulacoes[chave] += int(valor)
    for anulacao in anulacoes_unicas:
        chave = str(anulacao["chave_empenho"])
        valor = int(anulacao["valor_anulacao_centavos"])
        if valor < 0:
            problemas.append(
                _problema(
                    "VALOR_ANULACAO_NEGATIVO",
                    "Anulação possui valor negativo.",
                    chave=chave,
                    valor_centavos=valor,
                )
            )
        totais_anulacoes[chave] += valor

    for chave, valor_anulado in totais_anulacoes.items():
        valor_empenhado = valores_empenhos.get(chave)
        if valor_empenhado is None:
            problemas.append(
                _problema(
                    "ANULACAO_ORFA",
                    "Anulação não possui empenho publicado ou presente no lote.",
                    chave=chave,
                )
            )
        elif valor_empenhado - valor_anulado < 0:
            problemas.append(
                _problema(
                    "VALOR_LIQUIDO_NEGATIVO",
                    "A soma das anulações supera o valor do empenho.",
                    chave=chave,
                    valor_empenhado_centavos=valor_empenhado,
                    valor_anulado_centavos=valor_anulado,
                )
            )

    return LoteTceValidado(
        empenhos=empenhos_unicos,
        anulacoes=anulacoes_unicas,
        problemas=problemas,
    )


def _bloquear_particao(session: Session, codigo_municipio_tce: str, competencia: str) -> None:
    # A validação soma anulações de competências diferentes. O lock precisa
    # serializar todas as publicações do município para essa leitura continuar
    # válida até o commit da transação.
    escopo = f"tce-despesas:{codigo_municipio_tce}"
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:escopo))"),
        {"escopo": escopo},
    )


def _lote_publicado_atual(
    session: Session,
    codigo_municipio_tce: str,
    competencia: str,
) -> TceDespesaIngestionRun | None:
    return session.scalar(
        select(TceDespesaIngestionRun)
        .where(
            TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
            TceDespesaIngestionRun.competencia == competencia,
            TceDespesaIngestionRun.status == STATUS_PUBLICADO,
        )
        .with_for_update()
    )


def _carregar_referencias_publicadas(
    session: Session,
    codigo_municipio_tce: str,
    competencia_excluida: str,
) -> tuple[dict[str, int], dict[str, int]]:
    filtro_lotes = (
        TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
        TceDespesaIngestionRun.competencia != competencia_excluida,
        TceDespesaIngestionRun.status == STATUS_PUBLICADO,
    )
    empenhos_rows = session.execute(
        select(TceEmpenho.chave_empenho, TceEmpenho.valor_empenhado_centavos)
        .join(TceDespesaIngestionRun, TceEmpenho.run_id == TceDespesaIngestionRun.id)
        .where(*filtro_lotes)
    ).all()
    valores_empenhos: dict[str, int] = {}
    for chave, valor in empenhos_rows:
        valor_inteiro = int(valor)
        anterior = valores_empenhos.get(chave)
        if anterior is not None and anterior != valor_inteiro:
            raise RuntimeError(f"Empenho publicado com valores conflitantes: {chave}")
        valores_empenhos[chave] = valor_inteiro

    anulacoes_rows = session.execute(
        select(
            TceAnulacaoEmpenho.chave_empenho,
            func.sum(TceAnulacaoEmpenho.valor_anulacao_centavos),
        )
        .join(TceDespesaIngestionRun, TceAnulacaoEmpenho.run_id == TceDespesaIngestionRun.id)
        .where(*filtro_lotes)
        .group_by(TceAnulacaoEmpenho.chave_empenho)
    ).all()
    valores_anulacoes = {str(chave): int(valor) for chave, valor in anulacoes_rows}
    return valores_empenhos, valores_anulacoes


def _modelo_empenho(run_id: int, registro: dict[str, Any]) -> TceEmpenho:
    return TceEmpenho(
        run_id=run_id,
        chave_empenho=registro["chave_empenho"],
        exercicio_orcamento=int(registro["exercicio_orcamento"]),
        codigo_orgao=registro["codigo_orgao"],
        codigo_unidade_orcamentaria=registro["codigo_unidade_orcamentaria"],
        data_empenho=date.fromisoformat(registro["data_empenho"]),
        numero_empenho=registro["numero_empenho"],
        codigo_natureza_despesa=registro["codigo_natureza_despesa"],
        codigo_elemento_despesa=registro["codigo_elemento_despesa"],
        natureza_considerada=bool(registro["natureza_considerada"]),
        valor_empenhado_centavos=int(registro["valor_empenhado_centavos"]),
        tipo_documento_fornecedor=registro["tipo_documento_fornecedor"],
        documento_fornecedor=registro.get("documento_fornecedor"),
        cnpj_fornecedor=registro.get("cnpj_fornecedor"),
        cpf_fornecedor=registro.get("cpf_fornecedor"),
        nome_fornecedor=registro.get("nome_fornecedor"),
        municipio_fornecedor_informado=registro.get("municipio_fornecedor_informado"),
        uf_fornecedor_informada=registro.get("uf_fornecedor_informada"),
        estado_empenho=registro.get("estado_empenho"),
        numero_nota_anulacao_informado=registro.get("numero_nota_anulacao_informado"),
        numero_empenho_substituto=registro.get("numero_empenho_substituto"),
        numero_contrato=registro.get("numero_contrato"),
        numero_licitacao=registro.get("numero_licitacao"),
        payload=_payload_json(registro.get("payload_bruto") or {}),
    )


def _modelo_anulacao(run_id: int, registro: dict[str, Any]) -> TceAnulacaoEmpenho:
    return TceAnulacaoEmpenho(
        run_id=run_id,
        chave_anulacao=registro["chave_anulacao"],
        chave_empenho=registro["chave_empenho"],
        data_empenho=date.fromisoformat(registro["data_empenho"]),
        numero_empenho=registro["numero_empenho"],
        numero_anulacao=registro["numero_anulacao"],
        data_anulacao=date.fromisoformat(registro["data_anulacao"]),
        modalidade_anulacao=registro.get("modalidade_anulacao"),
        descricao_anulacao=registro.get("descricao_anulacao"),
        valor_anulacao_centavos=int(registro["valor_anulacao_centavos"]),
        payload=_payload_json(registro.get("payload_bruto") or {}),
    )


def _remover_lotes_expirados_session(session: Session, agora: datetime) -> int:
    resultado = session.execute(
        delete(TceDespesaIngestionRun).where(
            TceDespesaIngestionRun.status.in_((STATUS_SUBSTITUIDO, STATUS_REJEITADO)),
            TceDespesaIngestionRun.expira_em.is_not(None),
            TceDespesaIngestionRun.expira_em <= agora,
        )
    )
    return int(resultado.rowcount or 0)


def remover_lotes_expirados(*, agora: datetime | None = None) -> int:
    database.init_db()
    instante = agora or _agora_utc()
    with orm.main_session() as session:
        return _remover_lotes_expirados_session(session, instante)


def listar_publicacoes_competencias(
    *,
    codigo_municipio_tce: str,
    competencias: Iterable[str],
) -> dict[str, datetime | None]:
    """Retorna o instante da publicacao ativa de cada competencia solicitada."""
    competencias_unicas = tuple(dict.fromkeys(str(item) for item in competencias))
    if not competencias_unicas:
        return {}

    database.init_db()
    consulta = select(
        TceDespesaIngestionRun.competencia,
        TceDespesaIngestionRun.publicado_em,
    ).where(
        TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
        TceDespesaIngestionRun.status == STATUS_PUBLICADO,
        TceDespesaIngestionRun.competencia.in_(competencias_unicas),
    )
    with orm.main_session() as session:
        registros = session.execute(consulta).all()

    return {str(competencia): publicado_em for competencia, publicado_em in registros}


def listar_empenhos_liquidos_publicados(
    *,
    codigo_municipio_tce: str | None,
    data_inicial: date,
    data_final: date,
    uf: str | None = None,
) -> list[dict[str, Any]]:
    """Lê empenhos ativos e desconta todas as anulações atualmente publicadas."""
    if data_inicial > data_final:
        raise ValueError("data_inicial deve ser menor ou igual a data_final.")

    database.init_db()
    filtros_anulacoes = [TceDespesaIngestionRun.status == STATUS_PUBLICADO]
    if codigo_municipio_tce is not None:
        filtros_anulacoes.append(
            TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce
        )
    anulacoes_publicadas = (
        select(
            TceDespesaIngestionRun.codigo_municipio_tce.label("codigo_municipio_tce"),
            TceAnulacaoEmpenho.chave_empenho.label("chave_empenho"),
            func.sum(TceAnulacaoEmpenho.valor_anulacao_centavos).label(
                "valor_anulado_centavos"
            ),
        )
        .join(
            TceDespesaIngestionRun,
            TceAnulacaoEmpenho.run_id == TceDespesaIngestionRun.id,
        )
        .where(*filtros_anulacoes)
        .group_by(
            TceDespesaIngestionRun.codigo_municipio_tce,
            TceAnulacaoEmpenho.chave_empenho,
        )
        .subquery()
    )
    valor_anulado = func.coalesce(anulacoes_publicadas.c.valor_anulado_centavos, 0)
    consulta = (
        select(
            TceDespesaIngestionRun.codigo_municipio_tce,
            TceMunicipio.codigo_municipio_ibge,
            IbgeMunicipio.nome.label("municipio_comprador"),
            IbgeMunicipio.uf.label("uf_comprador"),
            TceEmpenho.chave_empenho,
            TceEmpenho.exercicio_orcamento,
            TceEmpenho.codigo_orgao,
            TceEmpenho.codigo_unidade_orcamentaria,
            TceEmpenho.data_empenho,
            TceEmpenho.numero_empenho,
            TceEmpenho.codigo_natureza_despesa,
            TceEmpenho.codigo_elemento_despesa,
            TceEmpenho.valor_empenhado_centavos,
            valor_anulado.label("valor_anulado_centavos"),
            (TceEmpenho.valor_empenhado_centavos - valor_anulado).label(
                "valor_liquido_centavos"
            ),
            TceEmpenho.tipo_documento_fornecedor,
            TceEmpenho.documento_fornecedor,
            TceEmpenho.cnpj_fornecedor,
            TceEmpenho.cpf_fornecedor,
            TceEmpenho.nome_fornecedor,
            TceEmpenho.municipio_fornecedor_informado,
            TceEmpenho.uf_fornecedor_informada,
            TceEmpenho.estado_empenho,
        )
        .join(
            TceDespesaIngestionRun,
            TceEmpenho.run_id == TceDespesaIngestionRun.id,
        )
        .join(
            TceMunicipio,
            TceMunicipio.codigo_municipio_tce
            == TceDespesaIngestionRun.codigo_municipio_tce,
        )
        .join(
            IbgeMunicipio,
            IbgeMunicipio.codigo_municipio == TceMunicipio.codigo_municipio_ibge,
        )
        .outerjoin(
            anulacoes_publicadas,
            (
                anulacoes_publicadas.c.codigo_municipio_tce
                == TceDespesaIngestionRun.codigo_municipio_tce
            )
            & (anulacoes_publicadas.c.chave_empenho == TceEmpenho.chave_empenho),
        )
        .where(
            TceDespesaIngestionRun.status == STATUS_PUBLICADO,
            TceEmpenho.natureza_considerada.is_(True),
            TceEmpenho.data_empenho >= data_inicial,
            TceEmpenho.data_empenho <= data_final,
        )
        .order_by(TceEmpenho.data_empenho, TceEmpenho.chave_empenho)
    )
    if codigo_municipio_tce is not None:
        consulta = consulta.where(
            TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce
        )
    if uf is not None:
        consulta = consulta.where(IbgeMunicipio.uf == uf.upper())

    with orm.main_session() as session:
        registros = session.execute(consulta).mappings().all()

    return [
        {
            **dict(registro),
            "codigo_municipio_tce": registro.get("codigo_municipio_tce")
            or codigo_municipio_tce,
            "data_empenho": registro["data_empenho"].isoformat(),
            "valor_empenhado_centavos": int(registro["valor_empenhado_centavos"]),
            "valor_anulado_centavos": int(registro["valor_anulado_centavos"]),
            "valor_liquido_centavos": int(registro["valor_liquido_centavos"]),
        }
        for registro in registros
    ]


def listar_publicacoes_competencias_estado(
    *,
    competencias: Iterable[str],
    uf: str,
) -> list[dict[str, Any]]:
    """Lista as particoes municipais publicadas no periodo para uma UF."""
    competencias_unicas = tuple(dict.fromkeys(str(item) for item in competencias))
    if not competencias_unicas:
        return []
    database.init_db()
    consulta = (
        select(
            TceDespesaIngestionRun.codigo_municipio_tce,
            TceMunicipio.codigo_municipio_ibge,
            IbgeMunicipio.nome.label("municipio"),
            IbgeMunicipio.uf,
            TceDespesaIngestionRun.competencia,
            TceDespesaIngestionRun.publicado_em,
        )
        .join(
            TceMunicipio,
            TceMunicipio.codigo_municipio_tce
            == TceDespesaIngestionRun.codigo_municipio_tce,
        )
        .join(
            IbgeMunicipio,
            IbgeMunicipio.codigo_municipio == TceMunicipio.codigo_municipio_ibge,
        )
        .where(
            TceDespesaIngestionRun.status == STATUS_PUBLICADO,
            TceDespesaIngestionRun.competencia.in_(competencias_unicas),
            IbgeMunicipio.uf == uf.upper(),
        )
        .order_by(
            TceDespesaIngestionRun.competencia,
            TceDespesaIngestionRun.codigo_municipio_tce,
        )
    )
    with orm.main_session() as session:
        registros = session.execute(consulta).mappings().all()
    return [
        {
            **dict(item),
            "publicado_em": item["publicado_em"].isoformat()
            if item.get("publicado_em") is not None
            else None,
        }
        for item in registros
    ]


def _texto_normalizado_sql(expressao: Any) -> Any:
    return func.translate(
        func.upper(func.trim(expressao)),
        "ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
        "AAAAAEEEEIIIIOOOOOUUUUC",
    )


def _expressoes_empenhos_publicados() -> dict[str, Any]:
    dados = FornecedorCache.dados_normalizados
    porte = func.coalesce(dados["porte_padronizado"].astext, "NAO_IDENTIFICADO")
    municipio_fornecedor = dados["municipio_sede"].astext
    uf_fornecedor = func.upper(func.trim(dados["uf_sede"].astext))
    origem = case(
        (
            or_(
                FornecedorCache.cnpj.is_(None),
                uf_fornecedor.is_(None),
                uf_fornecedor == "",
                (
                    (uf_fornecedor == "CE")
                    & or_(municipio_fornecedor.is_(None), func.trim(municipio_fornecedor) == "")
                ),
            ),
            "ORIGEM_NAO_IDENTIFICADA",
        ),
        (uf_fornecedor != "CE", "FORA_DO_CEARA"),
        (
            _texto_normalizado_sql(municipio_fornecedor)
            == _texto_normalizado_sql(IbgeMunicipio.nome),
            "NO_MUNICIPIO_COMPRADOR",
        ),
        else_="EM_OUTRO_MUNICIPIO",
    )
    return {
        "porte": porte,
        "municipio_fornecedor": municipio_fornecedor,
        "uf_fornecedor": uf_fornecedor,
        "origem": origem,
    }


def _subconsulta_anulacoes_publicadas(
    *,
    codigo_municipio_tce: str | None = None,
    chave_empenho: str | None = None,
) -> Any:
    filtros = [TceDespesaIngestionRun.status == STATUS_PUBLICADO]
    if codigo_municipio_tce:
        filtros.append(
            TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce
        )
    if chave_empenho:
        filtros.append(TceAnulacaoEmpenho.chave_empenho == chave_empenho)
    return (
        select(
            TceDespesaIngestionRun.codigo_municipio_tce.label("codigo_municipio_tce"),
            TceAnulacaoEmpenho.chave_empenho.label("chave_empenho"),
            func.sum(TceAnulacaoEmpenho.valor_anulacao_centavos).label(
                "valor_anulado_centavos"
            ),
            func.count(TceAnulacaoEmpenho.id).label("quantidade_anulacoes"),
        )
        .join(TceDespesaIngestionRun, TceAnulacaoEmpenho.run_id == TceDespesaIngestionRun.id)
        .where(*filtros)
        .group_by(
            TceDespesaIngestionRun.codigo_municipio_tce,
            TceAnulacaoEmpenho.chave_empenho,
        )
        .subquery()
    )


def _serializar_registro_empenho(registro: Any) -> dict[str, Any]:
    resultado = dict(registro)
    resultado.pop("_total_items", None)
    for campo in ("data_empenho", "publicado_em", "fornecedor_observado_em"):
        valor = resultado.get(campo)
        if valor is not None:
            resultado[campo] = valor.isoformat()
    for campo in (
        "valor_empenhado_centavos",
        "valor_anulado_centavos",
        "valor_liquido_centavos",
        "quantidade_anulacoes",
        "run_id",
        "exercicio_orcamento",
    ):
        if resultado.get(campo) is not None:
            resultado[campo] = int(resultado[campo])
    return resultado


def listar_empenhos_publicados_paginados(
    *,
    data_inicial: date,
    data_final: date,
    codigo_municipio_tce: str | None,
    portes: list[str],
    origens: list[str],
    elementos: list[str],
    busca: str | None,
    pagina: int,
    tamanho_pagina: int,
    ordenar_por: str,
    ordem: str,
    tipo_documento_fornecedor: str | None = None,
    documento_fornecedor: str | None = None,
) -> dict[str, Any]:
    """Lista somente empenhos monitorados pertencentes aos lotes publicados."""
    database.init_db()
    anulacoes = _subconsulta_anulacoes_publicadas(
        codigo_municipio_tce=codigo_municipio_tce
    )
    expressoes = _expressoes_empenhos_publicados()
    valor_anulado = func.coalesce(anulacoes.c.valor_anulado_centavos, 0)
    quantidade_anulacoes = func.coalesce(anulacoes.c.quantidade_anulacoes, 0)
    valor_liquido = TceEmpenho.valor_empenhado_centavos - valor_anulado

    filtros = [
        TceDespesaIngestionRun.status == STATUS_PUBLICADO,
        TceEmpenho.natureza_considerada.is_(True),
        TceEmpenho.data_empenho >= data_inicial,
        TceEmpenho.data_empenho <= data_final,
        TceEmpenho.codigo_elemento_despesa.in_(elementos),
    ]
    if codigo_municipio_tce:
        filtros.append(TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce)
    if tipo_documento_fornecedor:
        filtros.append(
            TceEmpenho.tipo_documento_fornecedor == tipo_documento_fornecedor
        )
    if documento_fornecedor:
        filtros.append(TceEmpenho.documento_fornecedor == documento_fornecedor)
    if portes:
        filtros.append(expressoes["porte"].in_(portes))
    if origens:
        filtros.append(expressoes["origem"].in_(origens))
    if busca and busca.strip():
        termo = f"%{busca.strip()}%"
        filtros.append(
            or_(
                TceEmpenho.numero_empenho.ilike(termo),
                TceEmpenho.chave_empenho.ilike(termo),
                TceEmpenho.nome_fornecedor.ilike(termo),
                TceEmpenho.documento_fornecedor.ilike(termo),
            )
        )

    joins = (
        (TceDespesaIngestionRun, TceEmpenho.run_id == TceDespesaIngestionRun.id),
        (TceMunicipio, TceMunicipio.codigo_municipio_tce == TceDespesaIngestionRun.codigo_municipio_tce),
        (IbgeMunicipio, IbgeMunicipio.codigo_municipio == TceMunicipio.codigo_municipio_ibge),
    )
    consulta = select(
        TceEmpenho.chave_empenho,
        TceEmpenho.exercicio_orcamento,
        TceEmpenho.data_empenho,
        TceEmpenho.numero_empenho,
        TceEmpenho.codigo_natureza_despesa,
        TceEmpenho.codigo_elemento_despesa,
        TceEmpenho.valor_empenhado_centavos,
        TceEmpenho.documento_fornecedor,
        TceEmpenho.cnpj_fornecedor,
        TceEmpenho.nome_fornecedor,
        TceDespesaIngestionRun.codigo_municipio_tce,
        TceMunicipio.codigo_municipio_ibge,
        IbgeMunicipio.nome.label("municipio_comprador"),
        IbgeMunicipio.uf.label("uf_comprador"),
        expressoes["porte"].label("porte_fornecedor"),
        expressoes["origem"].label("origem_fornecedor"),
        valor_anulado.label("valor_anulado_centavos"),
        valor_liquido.label("valor_liquido_centavos"),
        quantidade_anulacoes.label("quantidade_anulacoes"),
        func.count().over().label("_total_items"),
    )
    for tabela, condicao in joins:
        consulta = consulta.join(tabela, condicao)
    consulta = (
        consulta.outerjoin(FornecedorCache, FornecedorCache.cnpj == TceEmpenho.cnpj_fornecedor)
        .outerjoin(
            anulacoes,
            (anulacoes.c.codigo_municipio_tce == TceDespesaIngestionRun.codigo_municipio_tce)
            & (anulacoes.c.chave_empenho == TceEmpenho.chave_empenho),
        )
        .where(*filtros)
    )
    ordenacoes = {
        "date": TceEmpenho.data_empenho,
        "municipality": IbgeMunicipio.nome,
        "supplier": TceEmpenho.nome_fornecedor,
        "expense_element": TceEmpenho.codigo_elemento_despesa,
        "gross_value": TceEmpenho.valor_empenhado_centavos,
        "cancelled_value": valor_anulado,
        "net_value": valor_liquido,
    }
    coluna_ordem = ordenacoes[ordenar_por]
    direcao = coluna_ordem.asc() if ordem == "asc" else coluna_ordem.desc()
    consulta = consulta.order_by(direcao, TceEmpenho.chave_empenho.asc()).limit(
        tamanho_pagina
    ).offset((pagina - 1) * tamanho_pagina)

    contagem = select(func.count(TceEmpenho.id))
    for tabela, condicao in joins:
        contagem = contagem.join(tabela, condicao)
    contagem = contagem.outerjoin(
        FornecedorCache, FornecedorCache.cnpj == TceEmpenho.cnpj_fornecedor
    ).where(*filtros)
    with orm.main_session() as session:
        registros = session.execute(consulta).mappings().all()
        if registros:
            total = int(registros[0]["_total_items"])
        elif pagina == 1:
            total = 0
        else:
            total = int(session.scalar(contagem) or 0)
    return {
        "total": total,
        "registros": [_serializar_registro_empenho(item) for item in registros],
    }


def obter_empenho_publicado_por_chave(chave_empenho: str) -> dict[str, Any] | None:
    database.init_db()
    anulacoes = _subconsulta_anulacoes_publicadas(chave_empenho=chave_empenho)
    expressoes = _expressoes_empenhos_publicados()
    valor_anulado = func.coalesce(anulacoes.c.valor_anulado_centavos, 0)
    quantidade_anulacoes = func.coalesce(anulacoes.c.quantidade_anulacoes, 0)
    consulta = (
        select(
            TceEmpenho,
            TceDespesaIngestionRun.codigo_municipio_tce,
            TceDespesaIngestionRun.competencia,
            TceDespesaIngestionRun.status.label("status_publicacao"),
            TceDespesaIngestionRun.publicado_em,
            TceMunicipio.codigo_municipio_ibge,
            IbgeMunicipio.nome.label("municipio_comprador"),
            IbgeMunicipio.uf.label("uf_comprador"),
            expressoes["porte"].label("porte_fornecedor"),
            expressoes["origem"].label("origem_fornecedor"),
            expressoes["municipio_fornecedor"].label("municipio_fornecedor"),
            expressoes["uf_fornecedor"].label("uf_fornecedor"),
            FornecedorCache.dados_normalizados["cnae_principal_codigo"].astext.label("cnae_principal_codigo"),
            FornecedorCache.dados_normalizados["cnae_principal_descricao"].astext.label("cnae_principal_descricao"),
            FornecedorCache.observado_em.label("fornecedor_observado_em"),
            valor_anulado.label("valor_anulado_centavos"),
            (TceEmpenho.valor_empenhado_centavos - valor_anulado).label("valor_liquido_centavos"),
            quantidade_anulacoes.label("quantidade_anulacoes"),
        )
        .join(TceDespesaIngestionRun, TceEmpenho.run_id == TceDespesaIngestionRun.id)
        .join(TceMunicipio, TceMunicipio.codigo_municipio_tce == TceDespesaIngestionRun.codigo_municipio_tce)
        .join(IbgeMunicipio, IbgeMunicipio.codigo_municipio == TceMunicipio.codigo_municipio_ibge)
        .outerjoin(FornecedorCache, FornecedorCache.cnpj == TceEmpenho.cnpj_fornecedor)
        .outerjoin(
            anulacoes,
            (anulacoes.c.codigo_municipio_tce == TceDespesaIngestionRun.codigo_municipio_tce)
            & (anulacoes.c.chave_empenho == TceEmpenho.chave_empenho),
        )
        .where(
            TceDespesaIngestionRun.status == STATUS_PUBLICADO,
            TceEmpenho.natureza_considerada.is_(True),
            TceEmpenho.chave_empenho == chave_empenho,
        )
        .order_by(TceDespesaIngestionRun.publicado_em.desc())
    )
    with orm.main_session() as session:
        registro = session.execute(consulta).mappings().first()
    if registro is None:
        return None
    resultado = dict(registro)
    empenho = resultado.pop("TceEmpenho")
    for coluna in TceEmpenho.__table__.columns:
        if coluna.name != "payload":
            resultado.setdefault(coluna.name, getattr(empenho, coluna.name))
    return _serializar_registro_empenho(resultado)


def listar_anulacoes_publicadas_por_empenho(
    *,
    chave_empenho: str,
    codigo_municipio_tce: str,
) -> list[dict[str, Any]]:
    database.init_db()
    consulta = (
        select(
            TceAnulacaoEmpenho.chave_anulacao,
            TceAnulacaoEmpenho.numero_anulacao,
            TceAnulacaoEmpenho.data_anulacao,
            TceAnulacaoEmpenho.modalidade_anulacao,
            TceAnulacaoEmpenho.descricao_anulacao,
            TceAnulacaoEmpenho.valor_anulacao_centavos,
        )
        .join(TceDespesaIngestionRun, TceAnulacaoEmpenho.run_id == TceDespesaIngestionRun.id)
        .where(
            TceDespesaIngestionRun.status == STATUS_PUBLICADO,
            TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
            TceAnulacaoEmpenho.chave_empenho == chave_empenho,
        )
        .order_by(TceAnulacaoEmpenho.data_anulacao, TceAnulacaoEmpenho.chave_anulacao)
    )
    with orm.main_session() as session:
        registros = session.execute(consulta).mappings().all()
    return [
        {
            **dict(item),
            "data_anulacao": item["data_anulacao"].isoformat(),
            "valor_anulacao_centavos": int(item["valor_anulacao_centavos"]),
        }
        for item in registros
    ]


def registrar_lote_rejeitado(
    *,
    codigo_municipio_tce: str,
    competencia: str,
    erro: str,
    iniciado_em: datetime | None = None,
) -> ResultadoPublicacaoTce:
    """Registra falha de coleta/mapeamento sem alterar o lote publicado."""
    database.init_db()
    inicio = iniciado_em or _agora_utc()
    agora = _agora_utc()
    problema = _problema("FALHA_INGESTAO", erro)
    with orm.main_session() as session:
        _bloquear_particao(session, codigo_municipio_tce, competencia)
        _remover_lotes_expirados_session(session, agora)
        lote_anterior = _lote_publicado_atual(session, codigo_municipio_tce, competencia)
        run = TceDespesaIngestionRun(
            codigo_municipio_tce=codigo_municipio_tce,
            competencia=competencia,
            status=STATUS_REJEITADO,
            iniciado_em=inicio,
            finalizado_em=agora,
            expira_em=agora + RETENCAO_LOTES_SUBSTITUIDOS,
            quantidade_empenhos=0,
            quantidade_anulacoes=0,
            problemas=[problema],
            erro=erro,
        )
        session.add(run)
        session.flush()
        return ResultadoPublicacaoTce(
            run_id=run.id,
            status=run.status,
            quantidade_empenhos=0,
            quantidade_anulacoes=0,
            problemas=[problema],
            run_anterior_id=lote_anterior.id if lote_anterior else None,
        )


def publicar_lote_tce(
    *,
    codigo_municipio_tce: str,
    competencia: str,
    empenhos: Iterable[dict[str, Any]],
    anulacoes: Iterable[dict[str, Any]],
    iniciado_em: datetime | None = None,
) -> ResultadoPublicacaoTce:
    """Valida, persiste e publica uma competência em uma única transação."""
    database.init_db()
    inicio = iniciado_em or _agora_utc()
    agora = _agora_utc()

    with orm.main_session() as session:
        _bloquear_particao(session, codigo_municipio_tce, competencia)
        _remover_lotes_expirados_session(session, agora)
        lote_anterior = _lote_publicado_atual(session, codigo_municipio_tce, competencia)
        valores_publicados, anulacoes_publicadas = _carregar_referencias_publicadas(
            session,
            codigo_municipio_tce,
            competencia,
        )
        lote = validar_lote_tce(
            codigo_municipio_tce=codigo_municipio_tce,
            competencia=competencia,
            empenhos=empenhos,
            anulacoes=anulacoes,
            valores_empenhos_publicados=valores_publicados,
            anulacoes_publicadas_centavos=anulacoes_publicadas,
        )

        run = TceDespesaIngestionRun(
            codigo_municipio_tce=codigo_municipio_tce,
            competencia=competencia,
            status=STATUS_EM_VALIDACAO,
            iniciado_em=inicio,
            quantidade_empenhos=len(lote.empenhos),
            quantidade_anulacoes=len(lote.anulacoes),
            problemas=_payload_json(lote.problemas),
        )
        session.add(run)
        session.flush()

        if not lote.valido:
            run.status = STATUS_REJEITADO
            run.finalizado_em = agora
            run.expira_em = agora + RETENCAO_LOTES_SUBSTITUIDOS
            session.flush()
            return ResultadoPublicacaoTce(
                run_id=run.id,
                status=run.status,
                quantidade_empenhos=len(lote.empenhos),
                quantidade_anulacoes=len(lote.anulacoes),
                problemas=lote.problemas,
                run_anterior_id=lote_anterior.id if lote_anterior else None,
            )

        session.add_all(_modelo_empenho(run.id, registro) for registro in lote.empenhos)
        session.add_all(_modelo_anulacao(run.id, registro) for registro in lote.anulacoes)
        session.flush()

        if lote_anterior is not None:
            lote_anterior.status = STATUS_SUBSTITUIDO
            lote_anterior.substituido_em = agora
            lote_anterior.expira_em = agora + RETENCAO_LOTES_SUBSTITUIDOS
            session.flush()

        run.status = STATUS_PUBLICADO
        run.finalizado_em = agora
        run.publicado_em = agora
        session.flush()

        return ResultadoPublicacaoTce(
            run_id=run.id,
            status=run.status,
            quantidade_empenhos=len(lote.empenhos),
            quantidade_anulacoes=len(lote.anulacoes),
            problemas=[],
            run_anterior_id=lote_anterior.id if lote_anterior else None,
        )


__all__ = [
    "LoteTceValidado",
    "ResultadoPublicacaoTce",
    "listar_anulacoes_publicadas_por_empenho",
    "listar_empenhos_liquidos_publicados",
    "listar_empenhos_publicados_paginados",
    "listar_publicacoes_competencias",
    "listar_publicacoes_competencias_estado",
    "obter_empenho_publicado_por_chave",
    "publicar_lote_tce",
    "registrar_lote_rejeitado",
    "remover_lotes_expirados",
    "validar_lote_tce",
]

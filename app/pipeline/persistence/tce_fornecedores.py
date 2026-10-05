"""Consultas locais e somente leitura para fornecedores do TCE-CE."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import case, func, or_, select

from app.core import database, orm
from app.core.models import (
    FornecedorCache,
    IbgeMunicipio,
    TceDespesaIngestionRun,
    TceEmpenho,
    TceMunicipio,
)
from app.pipeline.persistence.tce_despesas import (
    STATUS_PUBLICADO,
    _expressoes_empenhos_publicados,
    _subconsulta_anulacoes_publicadas,
)


def _contexto_consulta(
    *,
    data_inicial: date,
    data_final: date,
    codigo_municipio_tce: str | None,
    portes: list[str],
    origens: list[str],
    elementos: list[str],
    exigir_documento: bool = True,
) -> dict[str, Any]:
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
    if exigir_documento:
        filtros.extend(
            [
                TceEmpenho.documento_fornecedor.is_not(None),
                func.trim(TceEmpenho.documento_fornecedor) != "",
            ]
        )
    if codigo_municipio_tce:
        filtros.append(
            TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce
        )
    if portes:
        filtros.append(expressoes["porte"].in_(portes))
    if origens:
        filtros.append(expressoes["origem"].in_(origens))
    return {
        "anulacoes": anulacoes,
        "expressoes": expressoes,
        "valor_anulado": valor_anulado,
        "quantidade_anulacoes": quantidade_anulacoes,
        "valor_liquido": valor_liquido,
        "filtros": filtros,
    }


def _joins(consulta: Any, anulacoes: Any) -> Any:
    return (
        consulta.join(
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
            IbgeMunicipio.codigo_municipio
            == TceMunicipio.codigo_municipio_ibge,
        )
        .outerjoin(
            FornecedorCache,
            FornecedorCache.cnpj == TceEmpenho.cnpj_fornecedor,
        )
        .outerjoin(
            anulacoes,
            (
                anulacoes.c.codigo_municipio_tce
                == TceDespesaIngestionRun.codigo_municipio_tce
            )
            & (anulacoes.c.chave_empenho == TceEmpenho.chave_empenho),
        )
    )


def _grupo_fornecedor(expressoes: dict[str, Any]) -> tuple[Any, ...]:
    return (
        TceEmpenho.tipo_documento_fornecedor,
        TceEmpenho.documento_fornecedor,
        TceEmpenho.cnpj_fornecedor,
        expressoes["porte"],
        FornecedorCache.cnpj,
        FornecedorCache.opencnpj_status,
    )


def _serializar_agregado(registro: Any) -> dict[str, Any]:
    resultado = dict(registro)
    resultado.pop("_total_items", None)
    for campo in ("primeiro_empenho", "ultimo_empenho"):
        if resultado.get(campo) is not None:
            resultado[campo] = resultado[campo].isoformat()
    for campo in (
        "valor_empenhado_centavos",
        "valor_anulado_centavos",
        "valor_liquido_centavos",
        "quantidade_empenhos",
        "quantidade_anulacoes",
        "quantidade_municipios",
        "quantidade_contratos",
        "quantidade_licitacoes",
        "meses_com_movimento",
    ):
        if resultado.get(campo) is not None:
            resultado[campo] = int(resultado[campo])
    resultado["origens_no_recorte"] = sorted(resultado.get("origens_no_recorte") or [])
    return resultado


def listar_fornecedores_publicados_paginados(
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
) -> dict[str, Any]:
    database.init_db()
    contexto = _contexto_consulta(
        data_inicial=data_inicial,
        data_final=data_final,
        codigo_municipio_tce=codigo_municipio_tce,
        portes=portes,
        origens=origens,
        elementos=elementos,
    )
    expressoes = contexto["expressoes"]
    filtros = list(contexto["filtros"])
    if busca and busca.strip():
        termo = f"%{busca.strip()}%"
        filtros.append(
            or_(
                TceEmpenho.documento_fornecedor.ilike(termo),
                TceEmpenho.nome_fornecedor.ilike(termo),
                FornecedorCache.dados_normalizados["opencnpj_razao_social"].astext.ilike(
                    termo
                ),
                FornecedorCache.dados_normalizados["opencnpj_nome_fantasia"].astext.ilike(
                    termo
                ),
            )
        )

    valor_empenhado = func.sum(TceEmpenho.valor_empenhado_centavos)
    valor_anulado = func.sum(contexto["valor_anulado"])
    valor_liquido = func.sum(contexto["valor_liquido"])
    quantidade_empenhos = func.count(TceEmpenho.id)
    primeiro_empenho = func.min(TceEmpenho.data_empenho)
    ultimo_empenho = func.max(TceEmpenho.data_empenho)
    nome = func.max(TceEmpenho.nome_fornecedor)
    cadastro_status = case(
        (
            (FornecedorCache.cnpj.is_not(None))
            & (FornecedorCache.opencnpj_status == "ok"),
            "DISPONIVEL",
        ),
        else_="NAO_DISPONIVEL",
    )
    consulta = select(
        TceEmpenho.tipo_documento_fornecedor,
        TceEmpenho.documento_fornecedor,
        TceEmpenho.cnpj_fornecedor,
        nome.label("nome_fornecedor"),
        expressoes["porte"].label("porte_fornecedor"),
        cadastro_status.label("cadastro_status"),
        func.array_agg(func.distinct(expressoes["origem"])).label(
            "origens_no_recorte"
        ),
        valor_empenhado.label("valor_empenhado_centavos"),
        valor_anulado.label("valor_anulado_centavos"),
        valor_liquido.label("valor_liquido_centavos"),
        quantidade_empenhos.label("quantidade_empenhos"),
        func.sum(contexto["quantidade_anulacoes"]).label("quantidade_anulacoes"),
        primeiro_empenho.label("primeiro_empenho"),
        ultimo_empenho.label("ultimo_empenho"),
        func.count(
            func.distinct(TceDespesaIngestionRun.codigo_municipio_tce)
        ).label("quantidade_municipios"),
        func.count().over().label("_total_items"),
    )
    consulta = _joins(consulta, contexto["anulacoes"]).where(*filtros).group_by(
        *_grupo_fornecedor(expressoes)
    )
    ordenacoes = {
        "supplier": nome,
        "gross_value": valor_empenhado,
        "cancelled_value": valor_anulado,
        "net_value": valor_liquido,
        "commitments_count": quantidade_empenhos,
        "first_commitment": primeiro_empenho,
        "last_commitment": ultimo_empenho,
    }
    coluna = ordenacoes[ordenar_por]
    direcao = coluna.asc() if ordem == "asc" else coluna.desc()
    consulta = consulta.order_by(
        direcao,
        TceEmpenho.tipo_documento_fornecedor,
        TceEmpenho.documento_fornecedor,
    ).limit(tamanho_pagina).offset((pagina - 1) * tamanho_pagina)

    grupos = select(
        TceEmpenho.tipo_documento_fornecedor,
        TceEmpenho.documento_fornecedor,
        TceEmpenho.cnpj_fornecedor,
        expressoes["porte"],
        FornecedorCache.cnpj,
        FornecedorCache.opencnpj_status,
    )
    grupos = _joins(grupos, contexto["anulacoes"]).where(*filtros).group_by(
        *_grupo_fornecedor(expressoes)
    ).subquery()
    with orm.main_session() as session:
        registros = session.execute(consulta).mappings().all()
        if registros:
            total = int(registros[0]["_total_items"])
        elif pagina == 1:
            total = 0
        else:
            total = int(session.scalar(select(func.count()).select_from(grupos)) or 0)
    return {
        "total": total,
        "registros": [_serializar_agregado(item) for item in registros],
    }


def obter_resumo_fornecedor_publicado(
    *,
    tipo_documento: str,
    documento: str,
    data_inicial: date,
    data_final: date,
    codigo_municipio_tce: str | None,
    portes: list[str],
    origens: list[str],
    elementos: list[str],
) -> dict[str, Any] | None:
    database.init_db()
    contexto = _contexto_consulta(
        data_inicial=data_inicial,
        data_final=data_final,
        codigo_municipio_tce=codigo_municipio_tce,
        portes=portes,
        origens=origens,
        elementos=elementos,
        exigir_documento=False,
    )
    filtros_recorte = list(contexto["filtros"])
    filtros_fornecedor = [
        *filtros_recorte,
        TceEmpenho.tipo_documento_fornecedor == tipo_documento,
        TceEmpenho.documento_fornecedor == documento,
    ]
    resumo = select(
        TceEmpenho.tipo_documento_fornecedor,
        TceEmpenho.documento_fornecedor,
        func.max(TceEmpenho.cnpj_fornecedor).label("cnpj_fornecedor"),
        func.max(TceEmpenho.nome_fornecedor).label("nome_fornecedor"),
        func.max(contexto["expressoes"]["porte"]).label("porte_fornecedor"),
        func.array_agg(func.distinct(contexto["expressoes"]["origem"])).label(
            "origens_no_recorte"
        ),
        func.sum(TceEmpenho.valor_empenhado_centavos).label(
            "valor_empenhado_centavos"
        ),
        func.sum(contexto["valor_anulado"]).label("valor_anulado_centavos"),
        func.sum(contexto["valor_liquido"]).label("valor_liquido_centavos"),
        func.count(TceEmpenho.id).label("quantidade_empenhos"),
        func.sum(contexto["quantidade_anulacoes"]).label("quantidade_anulacoes"),
        func.count(func.distinct(func.nullif(TceEmpenho.numero_contrato, ""))).label(
            "quantidade_contratos"
        ),
        func.count(func.distinct(func.nullif(TceEmpenho.numero_licitacao, ""))).label(
            "quantidade_licitacoes"
        ),
        func.count(func.distinct(func.to_char(TceEmpenho.data_empenho, "YYYY-MM"))).label(
            "meses_com_movimento"
        ),
        func.count(
            func.distinct(TceDespesaIngestionRun.codigo_municipio_tce)
        ).label("quantidade_municipios"),
        func.min(TceEmpenho.data_empenho).label("primeiro_empenho"),
        func.max(TceEmpenho.data_empenho).label("ultimo_empenho"),
    )
    resumo = _joins(resumo, contexto["anulacoes"]).where(*filtros_fornecedor).group_by(
        TceEmpenho.tipo_documento_fornecedor,
        TceEmpenho.documento_fornecedor,
    )

    competencia = func.to_char(TceEmpenho.data_empenho, "YYYY-MM")
    mensal = select(
        competencia.label("competencia"),
        func.sum(TceEmpenho.valor_empenhado_centavos).label(
            "valor_empenhado_centavos"
        ),
        func.sum(contexto["valor_anulado"]).label("valor_anulado_centavos"),
        func.sum(contexto["valor_liquido"]).label("valor_liquido_centavos"),
        func.count(TceEmpenho.id).label("quantidade_empenhos"),
    )
    mensal = _joins(mensal, contexto["anulacoes"]).where(*filtros_fornecedor).group_by(
        competencia
    ).order_by(competencia)

    por_elemento = select(
        TceEmpenho.codigo_elemento_despesa,
        func.sum(TceEmpenho.valor_empenhado_centavos).label(
            "valor_empenhado_centavos"
        ),
        func.sum(contexto["valor_anulado"]).label("valor_anulado_centavos"),
        func.sum(contexto["valor_liquido"]).label("valor_liquido_centavos"),
        func.count(TceEmpenho.id).label("quantidade_empenhos"),
    )
    por_elemento = _joins(por_elemento, contexto["anulacoes"]).where(
        *filtros_fornecedor
    ).group_by(TceEmpenho.codigo_elemento_despesa).order_by(
        TceEmpenho.codigo_elemento_despesa
    )

    total_recorte = select(
        func.coalesce(func.sum(contexto["valor_liquido"]), 0)
    )
    total_recorte = _joins(total_recorte, contexto["anulacoes"]).where(
        *filtros_recorte
    )

    with orm.main_session() as session:
        registro = session.execute(resumo).mappings().first()
        if registro is None:
            return None
        series = session.execute(mensal).mappings().all()
        elementos_rows = session.execute(por_elemento).mappings().all()
        total = int(session.scalar(total_recorte) or 0)
        cnpj = registro["cnpj_fornecedor"]
        cache = session.get(FornecedorCache, cnpj) if cnpj else None

    resultado = _serializar_agregado(registro)
    resultado["evolucao_mensal"] = [
        _serializar_agregado(item) for item in series
    ]
    resultado["por_elemento_despesa"] = [
        _serializar_agregado(item) for item in elementos_rows
    ]
    resultado["valor_total_recorte_centavos"] = total
    resultado["cadastro"] = None
    if cache is not None and cache.opencnpj_status == "ok":
        resultado["cadastro"] = {
            "dados_normalizados": dict(cache.dados_normalizados or {}),
            "observado_em": cache.observado_em.isoformat(),
            "cache_desatualizado": bool(cache.cache_desatualizado),
            "ultima_tentativa_status": cache.ultima_tentativa_status,
        }
    return resultado


__all__ = [
    "listar_fornecedores_publicados_paginados",
    "obter_resumo_fornecedor_publicado",
]

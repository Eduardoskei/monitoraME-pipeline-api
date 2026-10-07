"""Analise territorial somente leitura sobre empenhos publicados do TCE-CE."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd

from app.core import database
from app.core.config import NATUREZAS_DESPESA_MONITORADAS
from app.pipeline import analitico, empenhos
from app.pipeline.enrichment.fornecedores import enriquecer_com_fornecedor, extrair_cnpjs_distintos
from app.pipeline.persistence import tce_despesas as persistence


ORIGENS = {
    "NO_MUNICIPIO_COMPRADOR": "Sediado no município comprador",
    "EM_OUTRO_MUNICIPIO": "Outro município do Ceará",
    "FORA_DO_CEARA": "Fora do estado",
    "ORIGEM_NAO_IDENTIFICADA": analitico.ORIGEM_NAO_IDENTIFICADA,
}
ORIGENS_INVERSAS = {valor: chave for chave, valor in ORIGENS.items()}
PORTES = ("MEI", "ME", "EPP", "DEMAIS", "NAO_IDENTIFICADO")


def _competencias(inicio: date, fim: date) -> list[str]:
    ano, mes = inicio.year, inicio.month
    resultado: list[str] = []
    while (ano, mes) <= (fim.year, fim.month):
        resultado.append(f"{ano:04d}-{mes:02d}")
        mes += 1
        if mes == 13:
            ano, mes = ano + 1, 1
    return resultado


def _percentual(numerador: int, denominador: int) -> float:
    if denominador == 0:
        return 0.0
    return round(float(Decimal(numerador) * Decimal(100) / Decimal(denominador)), 6)


def _centavos(serie: pd.Series) -> pd.Series:
    return (pd.to_numeric(serie, errors="coerce").fillna(0) * 100).round().astype("int64")


def _fornecedores_cache(cnpjs: list[str]) -> tuple[pd.DataFrame, set[str]]:
    if not cnpjs:
        return pd.DataFrame(), set()
    encontrados = database.listar_fornecedores_cache(cnpjs)
    registros: list[dict[str, Any]] = []
    for cnpj, cache in encontrados.items():
        dados = dict(cache.get("dados_normalizados") or {})
        dados.setdefault("cnpj", cnpj)
        registros.append(dados)
    return pd.DataFrame(registros), set(encontrados)


def _aplicar_filtros(
    base: pd.DataFrame,
    *,
    portes: list[str],
    origens: list[str],
    elementos: list[str],
) -> pd.DataFrame:
    resultado = base.copy()
    if portes:
        resultado = resultado.loc[resultado["porte_fornecedor"].isin(portes)]
    if origens:
        origens_internas = {ORIGENS[item] for item in origens}
        resultado = resultado.loc[resultado["origem_geografica"].isin(origens_internas)]
    resultado = resultado.loc[
        resultado["natureza_despesa_codigo"].astype("string").isin(elementos)
    ]
    return resultado.reset_index(drop=True)


def _agrupar(
    base: pd.DataFrame,
    coluna: str,
    categorias: list[str],
    *,
    chave_saida: str,
) -> list[dict[str, Any]]:
    total = int(base["_liquido_centavos"].sum())
    resultado = []
    for categoria in categorias:
        recorte = base.loc[base[coluna].eq(categoria)]
        valor = int(recorte["_liquido_centavos"].sum())
        resultado.append({
            chave_saida: categoria,
            "valor_liquido_centavos": valor,
            "quantidade_empenhos": int(len(recorte)),
            "percentual": _percentual(valor, total),
        })
    return resultado


def _ranking_fornecedores(base: pd.DataFrame, limite: int, total: int) -> list[dict[str, Any]]:
    if base.empty:
        return []
    dados = base.copy()
    dados["_documento"] = dados["documento_fornecedor"].astype("string").fillna("")
    dados["_tipo"] = dados["tipo_documento_fornecedor"].astype("string").fillna("")
    dados["_nome"] = dados["nome_fornecedor"].astype("string").fillna("Não informado")
    agrupado = (
        dados.groupby(["_tipo", "_documento", "_nome"], dropna=False)
        .agg(
            valor_liquido_centavos=("_liquido_centavos", "sum"),
            quantidade_empenhos=("chave_empenho", "size"),
            porte=("porte_fornecedor", "first"),
            origem=("origem_geografica", "first"),
        )
        .reset_index()
        .sort_values(["valor_liquido_centavos", "quantidade_empenhos"], ascending=False)
        .head(limite)
    )
    return [
        {
            "posicao": posicao,
            "tipo_documento": (
                "CNPJ"
                if str(item["_tipo"]) in {"1", "CNPJ"}
                else "CPF"
                if str(item["_tipo"]) in {"2", "CPF"}
                else "NAO_IDENTIFICADO"
            ),
            "documento": str(item["_documento"]) or None,
            "nome": str(item["_nome"]),
            "porte": item["porte"],
            "origem": ORIGENS_INVERSAS.get(item["origem"], "ORIGEM_NAO_IDENTIFICADA"),
            "valor_liquido_centavos": int(item["valor_liquido_centavos"]),
            "quantidade_empenhos": int(item["quantidade_empenhos"]),
            "percentual": _percentual(int(item["valor_liquido_centavos"]), total),
        }
        for posicao, (_, item) in enumerate(agrupado.iterrows(), start=1)
    ]


def _concentracao(base: pd.DataFrame, total: int) -> dict[str, Any]:
    identificados = base.loc[
        base["documento_fornecedor"].astype("string").fillna("").str.strip().ne("")
    ]
    valores = (
        identificados.groupby(["tipo_documento_fornecedor", "documento_fornecedor"], dropna=False)[
            "_liquido_centavos"
        ]
        .sum()
        .sort_values(ascending=False)
    )
    total_identificado = int(valores.sum())
    participacoes = (
        [float(valor) * 100 / total_identificado for valor in valores]
        if total_identificado
        else []
    )
    hhi = round(sum(valor * valor for valor in participacoes), 6)
    classificacao = "ALTA" if hhi >= 2500 else "MODERADA" if hhi >= 1500 else "BAIXA"
    return {
        "fornecedores_considerados": int(len(valores)),
        "valor_fornecedores_identificados_centavos": total_identificado,
        "cobertura_por_valor_percentual": _percentual(total_identificado, total),
        "top_1_percentual": round(sum(participacoes[:1]), 6),
        "top_5_percentual": round(sum(participacoes[:5]), 6),
        "top_10_percentual": round(sum(participacoes[:10]), 6),
        "hhi": hhi,
        "classificacao": classificacao,
    }


def consultar_analise_territorial(
    *,
    start_date: str,
    end_date: str,
    uf: str,
    municipality_tce_code: str,
    company_sizes: list[str],
    supplier_origins: list[str],
    expense_element_codes: list[str],
    ranking_limit: int,
) -> dict[str, Any]:
    inicio, fim, municipio, portes, elementos = empenhos.preparar_filtros(
        start_date=start_date,
        end_date=end_date,
        uf=uf,
        municipality_tce_code=municipality_tce_code,
        company_sizes=company_sizes,
        expense_element_codes=expense_element_codes,
    )
    assert municipio is not None
    competencias_solicitadas = _competencias(inicio, fim)
    publicacoes = persistence.listar_publicacoes_competencias(
        codigo_municipio_tce=municipality_tce_code,
        competencias=competencias_solicitadas,
    )
    competencias_publicadas = [item for item in competencias_solicitadas if item in publicacoes]
    competencias_ausentes = [item for item in competencias_solicitadas if item not in publicacoes]
    if not competencias_publicadas:
        raise empenhos.EmpenhoRequestError(
            "TERRITORIAL_DATA_NOT_AVAILABLE",
            "O municipio nao possui competencias publicadas no periodo solicitado.",
            [{"field": "filters", "reason": "no_published_competence"}],
        )

    registros = persistence.listar_empenhos_liquidos_publicados(
        codigo_municipio_tce=municipality_tce_code,
        data_inicial=inicio,
        data_final=fim,
    )
    base_tce = pd.DataFrame(registros)
    cnpjs = extrair_cnpjs_distintos(base_tce.get("cnpj_fornecedor")) if not base_tce.empty else []
    fornecedores, cnpjs_cache = _fornecedores_cache(cnpjs)
    if not base_tce.empty and not fornecedores.empty:
        base_tce = enriquecer_com_fornecedor(base_tce, fornecedores, coluna_cnpj="cnpj_fornecedor")
    base = analitico.montar_base_analitica_tce(
        base_tce,
        codigo_municipio=municipality_tce_code,
        municipio_comprador=municipio["nome"],
        uf_comprador=uf.upper(),
    )
    base = _aplicar_filtros(
        base,
        portes=portes,
        origens=supplier_origins,
        elementos=elementos,
    )
    base["_bruto_centavos"] = _centavos(base["valor_empenhado"])
    base["_anulado_centavos"] = _centavos(base["valor_anulado"])
    base["_liquido_centavos"] = _centavos(base["valor"])
    if bool(base["_liquido_centavos"].lt(0).any()):
        raise empenhos.EmpenhoRequestError(
            "INVALID_PUBLISHED_DATA",
            "Existem empenhos publicados com valor liquido negativo no recorte.",
            [{"field": "valor_liquido_centavos", "reason": "negative_value"}],
        )

    bruto = int(base["_bruto_centavos"].sum())
    anulado = int(base["_anulado_centavos"].sum())
    liquido = int(base["_liquido_centavos"].sum())
    origens = _agrupar(
        base,
        "origem_geografica",
        list(ORIGENS.values()),
        chave_saida="origem_interna",
    )
    for item in origens:
        item["origem"] = ORIGENS_INVERSAS.get(
            item.pop("origem_interna"), "ORIGEM_NAO_IDENTIFICADA"
        )
    local = next(item["valor_liquido_centavos"] for item in origens if item["origem"] == "NO_MUNICIPIO_COMPRADOR")
    outro_ce = next(item["valor_liquido_centavos"] for item in origens if item["origem"] == "EM_OUTRO_MUNICIPIO")
    fora_ce = next(item["valor_liquido_centavos"] for item in origens if item["origem"] == "FORA_DO_CEARA")
    desconhecida = next(item["valor_liquido_centavos"] for item in origens if item["origem"] == "ORIGEM_NAO_IDENTIFICADA")

    por_porte = _agrupar(base, "porte_fornecedor", list(PORTES), chave_saida="porte")
    identificados_porte = sum(
        item["valor_liquido_centavos"] for item in por_porte if item["porte"] != "NAO_IDENTIFICADO"
    )
    me_mei = sum(
        item["valor_liquido_centavos"] for item in por_porte if item["porte"] in {"ME", "MEI"}
    )
    por_elemento = []
    for codigo in sorted(elementos, key=int):
        recorte = base.loc[base["natureza_despesa_codigo"].astype("string").eq(codigo)]
        valor = int(recorte["_liquido_centavos"].sum())
        por_elemento.append({
            "codigo": codigo,
            "nome": NATUREZAS_DESPESA_MONITORADAS[codigo],
            "valor_liquido_centavos": valor,
            "quantidade_empenhos": int(len(recorte)),
            "percentual": _percentual(valor, liquido),
        })

    evolucao = []
    for competencia in competencias_solicitadas:
        recorte = base.loc[base["ano_mes"].astype("string").eq(competencia)]
        valor = int(recorte["_liquido_centavos"].sum())
        valor_local = int(
            recorte.loc[recorte["origem_geografica"].eq(ORIGENS["NO_MUNICIPIO_COMPRADOR"]), "_liquido_centavos"].sum()
        )
        evolucao.append({
            "competencia": competencia,
            "publicada": competencia in publicacoes,
            "valor_liquido_centavos": valor if competencia in publicacoes else None,
            "valor_local_centavos": valor_local if competencia in publicacoes else None,
            "retencao_local_percentual": _percentual(valor_local, valor) if competencia in publicacoes else None,
        })

    com_cache = int(base["cnpj_fornecedor"].isin(cnpjs_cache).sum()) if not base.empty else 0
    valor_conhecido = liquido - desconhecida
    return {
        "fonte": "TCE-CE",
        "origem_dados": "BANCO_LOCAL",
        "consultas_externas": False,
        "unidade_monetaria": "CENTAVOS",
        "parametros": {
            "start_date": start_date,
            "end_date": end_date,
            "uf": uf.upper(),
            "municipality_tce_code": municipality_tce_code,
            "company_sizes": company_sizes,
            "supplier_origins": supplier_origins,
            "expense_element_codes": expense_element_codes,
            "ranking_limit": ranking_limit,
        },
        "municipio": {
            "codigo_tce": municipio["codigo_municipio_tce"],
            "codigo_ibge": municipio["codigo_municipio_ibge"],
            "nome": municipio["nome"],
            "uf": municipio["uf"],
        },
        "cobertura": {
            "competencias_solicitadas": competencias_solicitadas,
            "competencias_publicadas": competencias_publicadas,
            "competencias_ausentes": competencias_ausentes,
            "empenhos": int(len(base)),
            "empenhos_com_cache_cadastral": com_cache,
            "cobertura_cadastral_percentual": _percentual(com_cache, len(base)),
            "cobertura_geografica_por_valor_percentual": _percentual(valor_conhecido, liquido),
        },
        "resumo_financeiro": {
            "valor_empenhado_centavos": bruto,
            "valor_anulado_centavos": anulado,
            "valor_liquido_centavos": liquido,
            "quantidade_empenhos": int(len(base)),
            "quantidade_fornecedores_identificados": int(
                base.loc[base["documento_fornecedor"].astype("string").fillna("").str.strip().ne(""), "documento_fornecedor"].nunique()
            ),
        },
        "indicadores_territoriais": {
            "valor_local_centavos": local,
            "valor_outros_municipios_ce_centavos": outro_ce,
            "valor_fora_ceara_centavos": fora_ce,
            "valor_origem_nao_identificada_centavos": desconhecida,
            "retencao_local_percentual": _percentual(local, liquido),
            "retencao_local_origem_conhecida_percentual": _percentual(local, valor_conhecido),
            "evasao_municipal_percentual": _percentual(outro_ce + fora_ce, liquido),
            "retencao_ceara_percentual": _percentual(local + outro_ce, liquido),
            "participacao_me_mei_percentual": _percentual(me_mei, identificados_porte),
        },
        "destino_recursos": origens,
        "participacao_por_porte": por_porte,
        "elementos_despesa": por_elemento,
        "evolucao_mensal": evolucao,
        "concentracao_fornecedores": _concentracao(base, liquido),
        "ranking_fornecedores": _ranking_fornecedores(base, ranking_limit, liquido),
        "metodologia": {
            "base_financeira": "EMPENHO_LIQUIDO",
            "formula_valor_liquido": "valor_empenhado - valor_anulado",
            "formula_retencao_local": "valor_local / valor_liquido_total * 100",
            "formula_participacao_me_mei": "(ME + MEI) / (ME + MEI + EPP + DEMAIS) * 100",
            "escala_percentual": "0_A_100",
            "escopo_elementos": list(NATUREZAS_DESPESA_MONITORADAS),
        },
    }


__all__ = ["consultar_analise_territorial"]

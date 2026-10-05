from __future__ import annotations

from datetime import date
from itertools import zip_longest
from numbers import Number
from typing import Any

import pandas as pd

from app.core import database
from app.core.config import NATUREZAS_DESPESA_MONITORADAS, UF_PADRAO
from app.pipeline import analitico, kpis
from app.pipeline.enrichment.fornecedores import (
    enriquecer_com_fornecedor,
    extrair_cnpjs_distintos,
)
from app.pipeline.persistence import tce_despesas as tce_despesas_persistence


_PORTES_FILTRO = {
    "ME": "ME",
    "MEI": "MEI",
    "EPP": "EPP",
    "OTHER": "DEMAIS",
    "UNKNOWN": "NAO_IDENTIFICADO",
}
_ORIGENS_FILTRO = dict(kpis.DESTINOS_RECURSOS_OVERVIEW)


class ComparisonRequestError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        details: list[dict[str, Any]],
        *,
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status_code = status_code


def _data_iso(valor: str, *, campo: str) -> date:
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError) as error:
        raise ComparisonRequestError(
            "INVALID_DATE",
            f"{campo.split('.')[-1]} deve usar o formato YYYY-MM-DD.",
            [{"field": campo, "reason": "invalid_date", "value": valor}],
        ) from error


def _validar_datas(filtros: dict[str, Any], *, lado: str) -> tuple[date, date]:
    inicio = _data_iso(filtros["start_date"], campo=f"{lado}.filters.start_date")
    fim = _data_iso(filtros["end_date"], campo=f"{lado}.filters.end_date")
    if inicio > fim:
        raise ComparisonRequestError(
            "INVALID_DATE_RANGE",
            "start_date deve ser menor ou igual a end_date.",
            [
                {
                    "field": f"{lado}.filters.start_date",
                    "reason": "after_end_date",
                    "value": filtros["start_date"],
                }
            ],
        )
    return inicio, fim


def _competencias(inicio: date, fim: date) -> list[str]:
    resultado: list[str] = []
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        resultado.append(f"{ano:04d}-{mes:02d}")
        if mes == 12:
            ano, mes = ano + 1, 1
        else:
            mes += 1
    return resultado


def _validar_filtros(filtros: dict[str, Any], *, lado: str) -> tuple[date, date]:
    inicio, fim = _validar_datas(filtros, lado=lado)
    uf = str(filtros["uf"]).strip().upper()
    if uf != UF_PADRAO.upper():
        raise ComparisonRequestError(
            "UNSUPPORTED_UF",
            f"A comparacao possui dados apenas para a UF {UF_PADRAO.upper()}.",
            [
                {
                    "field": f"{lado}.filters.uf",
                    "reason": "unsupported_uf",
                    "value": filtros["uf"],
                }
            ],
        )

    codigos_invalidos = sorted(
        {
            str(codigo)
            for codigo in filtros.get("expense_element_codes", [])
            if str(codigo) not in NATUREZAS_DESPESA_MONITORADAS
        }
    )
    if codigos_invalidos:
        raise ComparisonRequestError(
            "INVALID_EXPENSE_ELEMENT_CODE",
            "expense_element_codes possui codigos nao monitorados.",
            [
                {
                    "field": f"{lado}.filters.expense_element_codes",
                    "reason": "unsupported_value",
                    "value": codigos_invalidos,
                }
            ],
        )
    return inicio, fim


def _validar_modo(payload: dict[str, Any]) -> None:
    esquerdo = payload["left"]["filters"]
    direito = payload["right"]["filters"]
    municipio_diferente = (
        esquerdo["uf"].upper(),
        esquerdo["municipality_ibge_code"],
    ) != (
        direito["uf"].upper(),
        direito["municipality_ibge_code"],
    )
    periodo_diferente = (
        esquerdo["start_date"],
        esquerdo["end_date"],
    ) != (
        direito["start_date"],
        direito["end_date"],
    )
    modo = payload["comparison_mode"]
    valido = {
        "PERIODS": periodo_diferente and not municipio_diferente,
        "MUNICIPALITIES": municipio_diferente and not periodo_diferente,
        "MIXED": municipio_diferente and periodo_diferente,
    }[modo]
    if valido:
        return

    raise ComparisonRequestError(
        "COMPARISON_MODE_MISMATCH",
        "comparison_mode nao corresponde aos municipios e periodos informados.",
        [
            {
                "field": "comparison_mode",
                "reason": "mode_mismatch",
                "value": modo,
            }
        ],
    )


def _fornecedores_somente_cache(cnpjs: list[str]) -> tuple[pd.DataFrame, set[str]]:
    if not cnpjs:
        return pd.DataFrame(), set()

    cache_por_cnpj = database.listar_fornecedores_cache(cnpjs)
    registros: list[dict[str, Any]] = []
    for cnpj in cnpjs:
        cache = cache_por_cnpj.get(cnpj)
        if cache is None:
            continue
        dados = dict(cache.get("dados_normalizados") or {})
        dados.setdefault("cnpj", cnpj)
        dados["cache_desatualizado"] = bool(cache.get("cache_desatualizado"))
        dados["cache_ultima_tentativa_status"] = cache.get("ultima_tentativa_status")
        registros.append(dados)
    return pd.DataFrame(registros), set(cache_por_cnpj)


def _aplicar_filtros(base: pd.DataFrame, filtros: dict[str, Any]) -> pd.DataFrame:
    resultado = base.copy()
    portes = filtros.get("company_sizes") or []
    if portes:
        internos = {_PORTES_FILTRO[str(porte)] for porte in portes}
        resultado = resultado.loc[resultado["porte_fornecedor"].isin(internos)]

    origens = filtros.get("supplier_origins") or []
    if origens:
        internas = {_ORIGENS_FILTRO[str(origem)] for origem in origens}
        resultado = resultado.loc[resultado["origem_geografica"].isin(internas)]

    elementos = filtros.get("expense_element_codes") or []
    if elementos:
        resultado = resultado.loc[
            resultado["natureza_despesa_codigo"].astype("string").isin(elementos)
        ]
    return resultado.reset_index(drop=True)


def _status_exercicio(inicio: date, fim: date) -> str:
    return "FECHADO" if fim.year < date.today().year else "EM_ANDAMENTO"


def _montar_overview_local(
    filtros: dict[str, Any],
    *,
    lado: str,
) -> dict[str, Any]:
    inicio, fim = _validar_filtros(filtros, lado=lado)
    uf = str(filtros["uf"]).strip().upper()
    codigo_ibge = str(filtros["municipality_ibge_code"]).strip()
    municipio = database.localizar_municipio_tce_por_ibge(codigo_ibge, uf=uf)
    if municipio is None:
        raise ComparisonRequestError(
            "MUNICIPALITY_NOT_FOUND",
            "Municipio nao encontrado no catalogo local.",
            [
                {
                    "field": f"{lado}.filters.municipality_ibge_code",
                    "reason": "not_found",
                    "value": codigo_ibge,
                }
            ],
            status_code=404,
        )

    codigo_tce = municipio["codigo_municipio_tce"]
    competencias_solicitadas = _competencias(inicio, fim)
    publicacoes = tce_despesas_persistence.listar_publicacoes_competencias(
        codigo_municipio_tce=codigo_tce,
        competencias=competencias_solicitadas,
    )
    competencias_publicadas = [
        competencia
        for competencia in competencias_solicitadas
        if competencia in publicacoes
    ]
    competencias_ausentes = [
        competencia
        for competencia in competencias_solicitadas
        if competencia not in publicacoes
    ]
    if not competencias_publicadas:
        raise ComparisonRequestError(
            "COMPARISON_DATA_NOT_AVAILABLE",
            "O municipio nao possui competencias publicadas no periodo solicitado.",
            [
                {
                    "field": f"{lado}.filters",
                    "reason": "no_published_competence",
                    "value": {
                        "municipality_ibge_code": codigo_ibge,
                        "start_date": filtros["start_date"],
                        "end_date": filtros["end_date"],
                    },
                }
            ],
        )

    empenhos = tce_despesas_persistence.listar_empenhos_liquidos_publicados(
        codigo_municipio_tce=codigo_tce,
        data_inicial=inicio,
        data_final=fim,
    )
    competencias_validas = set(competencias_publicadas)
    empenhos = [
        empenho
        for empenho in empenhos
        if str(empenho.get("data_empenho", ""))[:7] in competencias_validas
    ]

    base_tce = pd.DataFrame(empenhos)
    cnpjs = extrair_cnpjs_distintos(base_tce.get("cnpj_fornecedor")) if not base_tce.empty else []
    fornecedores_df, cnpjs_em_cache = _fornecedores_somente_cache(cnpjs)
    if not base_tce.empty and not fornecedores_df.empty:
        base_tce = enriquecer_com_fornecedor(
            base_tce,
            fornecedores_df,
            coluna_cnpj="cnpj_fornecedor",
        )

    base_analitica = analitico.montar_base_analitica_tce(
        base_tce,
        codigo_municipio=codigo_tce,
        municipio_comprador=municipio["nome"],
        uf_comprador=uf,
    )
    base_filtrada = _aplicar_filtros(base_analitica, filtros)
    overview = kpis.calcular_overview_me_mei(base_filtrada)
    registros_com_cache = (
        int(base_analitica["cnpj_fornecedor"].isin(cnpjs_em_cache).sum())
        if not base_analitica.empty
        else 0
    )
    cobertura_percentual = (
        (registros_com_cache / len(base_analitica)) * 100
        if len(base_analitica)
        else 0.0
    )

    return {
        "fonte": "TCE-CE",
        "origem_dados": "BANCO_LOCAL",
        "consultas_externas": False,
        "status_exercicio": _status_exercicio(inicio, fim),
        "parametros": {
            "data_inicial": filtros["start_date"],
            "data_final": filtros["end_date"],
            "uf": uf,
            "codigo_municipio_ibge": codigo_ibge,
            "codigo_municipio_tce": codigo_tce,
            "company_sizes": list(filtros.get("company_sizes") or []),
            "supplier_origins": list(filtros.get("supplier_origins") or []),
            "expense_element_codes": list(filtros.get("expense_element_codes") or []),
        },
        "municipio": {
            "nome": municipio["nome"],
            "uf": municipio["uf"],
            "codigo_ibge": municipio["codigo_municipio_ibge"],
            "codigo_tce": codigo_tce,
        },
        "competencias_solicitadas": competencias_solicitadas,
        "competencias_publicadas": competencias_publicadas,
        "competencias_atualizadas": [],
        "competencias_reutilizadas": competencias_publicadas,
        "competencias_desatualizadas": [],
        "competencias_ausentes": competencias_ausentes,
        "falhas_competencias": [],
        "politica_atualizacao": {
            "modo": "SOMENTE_BANCO",
            "atualizar_competencias": False,
            "atualizar_fornecedores": False,
        },
        "totais_brutos": {"empenhos": len(empenhos)},
        "totais_filtrados": {"empenhos": int(len(base_filtrada))},
        "cobertura_cadastral": {
            "empenhos": int(len(base_analitica)),
            "empenhos_com_cache": registros_com_cache,
            "percentual": cobertura_percentual,
        },
        "base_analitica": {
            "nome": "tce_empenhos_liquidos",
            "base_calculo": analitico.BASE_CALCULO_EMPENHOS_TCE,
            "municipio_comprador": municipio["nome"],
            "colunas": list(analitico.COLUNAS_BASE_ANALITICA_TCE),
        },
        "kpi": "overview_me_mei",
        "escopo": {
            "portes_considerados": list(kpis.PORTES_OVERVIEW),
            "portes_no_denominador_participacao": list(
                kpis.PORTES_COMPRAS_CONSIDERADAS_OVERVIEW
            ),
            "regra_participacao_me": "(ME + MEI) / (ME + MEI + EPP + DEMAIS)",
            "regra_geografica": "ME + MEI",
        },
        "unidade_monetaria": "CENTAVOS",
        **overview,
    }


def _valor_numerico(valor: Any) -> int | float | None:
    if isinstance(valor, bool) or not isinstance(valor, Number):
        return None
    return valor.item() if hasattr(valor, "item") else valor


def _comparar_valores(
    valor_esquerdo: Any,
    valor_direito: Any,
    *,
    unidade: str,
    label_esquerdo: str,
    label_direito: str,
) -> dict[str, Any]:
    esquerdo = _valor_numerico(valor_esquerdo)
    direito = _valor_numerico(valor_direito)
    valores = {
        "left": {"source": "LEFT", "label": label_esquerdo, "value": esquerdo},
        "right": {"source": "RIGHT", "label": label_direito, "value": direito},
    }
    if esquerdo is None or direito is None:
        return {
            "ordering": "UNAVAILABLE",
            **valores,
            "higher": None,
            "lower": None,
            "absolute_difference": None,
            "unit": unidade,
        }

    diferenca = abs(esquerdo - direito)
    if isinstance(esquerdo, int) and isinstance(direito, int):
        diferenca = int(diferenca)
    else:
        diferenca = round(float(diferenca), 12)
    if esquerdo == direito:
        return {
            "ordering": "TIE",
            **valores,
            "higher": valores["left"],
            "lower": valores["right"],
            "absolute_difference": diferenca,
            "unit": unidade,
        }

    maior, menor = (
        (valores["left"], valores["right"])
        if esquerdo > direito
        else (valores["right"], valores["left"])
    )
    return {
        "ordering": f"{maior['source']}_HIGHER",
        **valores,
        "higher": maior,
        "lower": menor,
        "absolute_difference": diferenca,
        "unit": unidade,
    }


def _indexar(registros: list[dict[str, Any]], chave: str) -> dict[str, dict[str, Any]]:
    return {str(registro[chave]): registro for registro in registros}


def _secoes(
    overview_esquerdo: dict[str, Any],
    overview_direito: dict[str, Any],
    *,
    label_esquerdo: str,
    label_direito: str,
) -> dict[str, Any]:
    def comparar(a: Any, b: Any, unidade: str) -> dict[str, Any]:
        return _comparar_valores(
            a,
            b,
            unidade=unidade,
            label_esquerdo=label_esquerdo,
            label_direito=label_direito,
        )

    kpis_esquerdo = overview_esquerdo["kpis"]
    kpis_direito = overview_direito["kpis"]
    secoes_kpis = []
    for indicador in kpis_esquerdo:
        unidade = "CENTAVOS" if indicador.endswith("_centavos") else "PONTOS_PERCENTUAIS"
        secoes_kpis.append(
            {
                "indicator": indicador,
                **comparar(kpis_esquerdo[indicador], kpis_direito.get(indicador), unidade),
            }
        )

    portes_esquerdo = _indexar(overview_esquerdo["participacao_por_porte_empresarial"], "porte")
    portes_direito = _indexar(overview_direito["participacao_por_porte_empresarial"], "porte")
    secoes_portes = []
    for porte in dict.fromkeys((*portes_esquerdo, *portes_direito)):
        esquerdo = portes_esquerdo.get(porte, {})
        direito = portes_direito.get(porte, {})
        secoes_portes.append(
            {
                "porte": porte,
                "valor_centavos": comparar(
                    esquerdo.get("valor_centavos"), direito.get("valor_centavos"), "CENTAVOS"
                ),
                "percentual": comparar(
                    esquerdo.get("percentual"), direito.get("percentual"), "PONTOS_PERCENTUAIS"
                ),
            }
        )

    elementos_esquerdo = _indexar(overview_esquerdo["elementos_despesa"], "codigo")
    elementos_direito = _indexar(overview_direito["elementos_despesa"], "codigo")
    secoes_elementos = []
    for codigo in dict.fromkeys((*elementos_esquerdo, *elementos_direito)):
        esquerdo = elementos_esquerdo.get(codigo, {})
        direito = elementos_direito.get(codigo, {})
        secoes_elementos.append(
            {
                "codigo": codigo,
                "nome": esquerdo.get("nome") or direito.get("nome"),
                **comparar(
                    esquerdo.get("valor_liquido_centavos"),
                    direito.get("valor_liquido_centavos"),
                    "CENTAVOS",
                ),
            }
        )

    destinos_esquerdo = _indexar(overview_esquerdo["destino_recursos"], "destino")
    destinos_direito = _indexar(overview_direito["destino_recursos"], "destino")
    secoes_destinos = []
    for destino in dict.fromkeys((*destinos_esquerdo, *destinos_direito)):
        esquerdo = destinos_esquerdo.get(destino, {})
        direito = destinos_direito.get(destino, {})
        secoes_destinos.append(
            {
                "destino": destino,
                "valor_centavos": comparar(
                    esquerdo.get("valor_centavos"), direito.get("valor_centavos"), "CENTAVOS"
                ),
                "percentual": comparar(
                    esquerdo.get("percentual"), direito.get("percentual"), "PONTOS_PERCENTUAIS"
                ),
            }
        )

    evolucao_esquerda = _indexar(
        overview_esquerdo["evolucao_compras_consideradas"], "periodo"
    )
    evolucao_direita = _indexar(
        overview_direito["evolucao_compras_consideradas"], "periodo"
    )
    publicadas_esquerda = set(overview_esquerdo["competencias_publicadas"])
    publicadas_direita = set(overview_direito["competencias_publicadas"])

    def registro_mensal(
        competencia: str | None,
        *,
        evolucao: dict[str, dict[str, Any]],
        publicadas: set[str],
    ) -> dict[str, Any] | None:
        if competencia is None or competencia not in publicadas:
            return None
        return evolucao.get(
            competencia,
            {
                "periodo": competencia,
                "compras_consideradas_centavos": 0,
                "microempresas_centavos": 0,
            },
        )

    serie_mensal = []
    for posicao, (competencia_esquerda, competencia_direita) in enumerate(
        zip_longest(
            overview_esquerdo["competencias_solicitadas"],
            overview_direito["competencias_solicitadas"],
            fillvalue=None,
        ),
        start=1,
    ):
        esquerdo = registro_mensal(
            competencia_esquerda,
            evolucao=evolucao_esquerda,
            publicadas=publicadas_esquerda,
        ) or {}
        direito = registro_mensal(
            competencia_direita,
            evolucao=evolucao_direita,
            publicadas=publicadas_direita,
        ) or {}
        serie_mensal.append(
            {
                "position": posicao,
                "left_period": competencia_esquerda,
                "right_period": competencia_direita,
                "left_published": competencia_esquerda in publicadas_esquerda,
                "right_published": competencia_direita in publicadas_direita,
                "compras_consideradas_centavos": comparar(
                    esquerdo.get("compras_consideradas_centavos"),
                    direito.get("compras_consideradas_centavos"),
                    "CENTAVOS",
                ),
                "microempresas_centavos": comparar(
                    esquerdo.get("microempresas_centavos"),
                    direito.get("microempresas_centavos"),
                    "CENTAVOS",
                ),
            }
        )

    return {
        "kpis": secoes_kpis,
        "participacao_por_porte_empresarial": secoes_portes,
        "elementos_despesa": secoes_elementos,
        "evolucao_compras_consideradas": serie_mensal,
        "destino_recursos": secoes_destinos,
    }


def consultar_comparacao_tce(payload: dict[str, Any]) -> dict[str, Any]:
    _validar_modo(payload)
    overview_esquerdo = _montar_overview_local(payload["left"]["filters"], lado="left")
    overview_direito = _montar_overview_local(payload["right"]["filters"], lado="right")
    label_esquerdo = payload["left"]["label"]
    label_direito = payload["right"]["label"]
    return {
        "fonte": "TCE-CE",
        "origem_dados": "BANCO_LOCAL",
        "consultas_externas": False,
        "comparison_mode": payload["comparison_mode"],
        "left": {
            "label": label_esquerdo,
            "filters": payload["left"]["filters"],
            "overview": overview_esquerdo,
        },
        "right": {
            "label": label_direito,
            "filters": payload["right"]["filters"],
            "overview": overview_direito,
        },
        "sections": _secoes(
            overview_esquerdo,
            overview_direito,
            label_esquerdo=label_esquerdo,
            label_direito=label_direito,
        ),
    }


__all__ = ["ComparisonRequestError", "consultar_comparacao_tce"]

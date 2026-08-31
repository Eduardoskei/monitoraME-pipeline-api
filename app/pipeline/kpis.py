"""
app/pipeline/kpis.py

Camada de agregacao (KPIs) sobre tabelas ja limpas/enriquecidas por
cleaners/ e merge.py — nao faz join nem limpeza, so agrupa e soma.

Uso tipico:
    df = merge.montar_base_tce(df_contratos, df_contratados, fornecedores_df=fornecedores_df)
    df["ano_mes"] = kpis.extrair_ano_mes(df["data_contrato"])
    resultado = kpis.calcular_participacao_me(
        df,
        colunas_agrupamento=["ano_mes"],
        coluna_valor="valor_total_contrato",
    )
"""

from __future__ import annotations

import pandas as pd

from app.utils import normalizar_chave_entidade, normalizar_cnpj


class DadosInsuficientesKPI(ValueError):
    """O KPI nao pode ser calculado sem inventar ou reinterpretar dados."""


def _validar_colunas(df: pd.DataFrame, colunas: list[str], origem: str) -> None:
    ausentes = [coluna for coluna in colunas if coluna not in df.columns]
    if ausentes:
        raise DadosInsuficientesKPI(
            f"[INDISPONIVEL] {origem}: campos necessarios nao existem: {', '.join(ausentes)}"
        )


def _eh_me_estrita(valor: object) -> bool:
    """Aceita apenas classificacoes inequivocas de Microempresa (ME)."""
    if pd.isna(valor):
        return False
    chave = normalizar_chave_entidade(valor)
    return chave in {"ME", "MICROEMPRESA", "MICRO EMPRESA"}


def calcular_participacao_me_local(
    licitacoes: pd.DataFrame,
    participantes: pd.DataFrame,
    *,
    coluna_licitacao: str,
    coluna_licitacao_participante: str,
    coluna_porte: str,
    coluna_municipio_empresa: str,
    coluna_municipio_comprador: str,
    coluna_cnpj: str | None = None,
    coluna_secretaria: str | None = None,
    coluna_data: str | None = None,
) -> dict[str, object]:
    """Calcula o KPI de participacao de ME local em licitacoes unicas.

    ``participantes`` precisa ter grao de proponente/concorrente. Tabelas de
    contratos, adjudicados ou vencedores nao devem ser passadas aqui. Os nomes
    das colunas sao obrigatoriamente informados pelo chamador para que o motor
    nao tente adivinhar o significado dos dados.
    """
    if licitacoes.empty:
        raise DadosInsuficientesKPI("[INDISPONIVEL] Nenhuma licitacao real foi fornecida.")
    if participantes.empty:
        raise DadosInsuficientesKPI(
            "[INDISPONIVEL] Nao ha participantes/proponentes; vencedores nao substituem participantes."
        )

    _validar_colunas(licitacoes, [coluna_licitacao, coluna_municipio_comprador], "licitacoes")
    _validar_colunas(
        participantes,
        [coluna_licitacao_participante, coluna_porte, coluna_municipio_empresa],
        "participantes",
    )
    opcionais = [c for c in (coluna_secretaria, coluna_data) if c]
    _validar_colunas(licitacoes, opcionais, "licitacoes")
    if coluna_cnpj:
        _validar_colunas(participantes, [coluna_cnpj], "participantes")

    cols_licitacao = [coluna_licitacao, coluna_municipio_comprador, *opcionais]
    base = licitacoes[cols_licitacao].copy()
    base = base.dropna(subset=[coluna_licitacao]).drop_duplicates(subset=[coluna_licitacao])
    if base.empty:
        raise DadosInsuficientesKPI("[INDISPONIVEL] Nao ha identificadores validos de licitacao.")

    props = participantes.copy()
    props = props.dropna(subset=[coluna_licitacao_participante])
    props["_eh_me"] = props[coluna_porte].map(_eh_me_estrita)
    props["_municipio_empresa"] = props[coluna_municipio_empresa].map(normalizar_chave_entidade)
    if coluna_cnpj:
        props["_cnpj"] = props[coluna_cnpj].map(normalizar_cnpj)

    dados = base.merge(
        props,
        left_on=coluna_licitacao,
        right_on=coluna_licitacao_participante,
        how="left",
        suffixes=("_licitacao", "_participante"),
    )
    comprador = f"{coluna_municipio_comprador}_licitacao" if coluna_municipio_comprador in props.columns else coluna_municipio_comprador
    dados["_municipio_comprador"] = dados[comprador].map(normalizar_chave_entidade)
    dados["_me_local"] = (
        dados["_eh_me"].fillna(False)
        & dados["_municipio_empresa"].notna()
        & dados["_municipio_comprador"].notna()
        & dados["_municipio_empresa"].eq(dados["_municipio_comprador"])
    )
    dados["_me_externa"] = (
        dados["_eh_me"].fillna(False)
        & dados["_municipio_empresa"].notna()
        & dados["_municipio_comprador"].notna()
        & dados["_municipio_empresa"].ne(dados["_municipio_comprador"])
    )

    flags = dados.groupby(coluna_licitacao, dropna=False).agg(
        com_me_local=("_me_local", "any"), com_me_externa=("_me_externa", "any")
    )
    total = len(base)
    com_local = int(flags["com_me_local"].sum())
    com_externa = int(flags["com_me_externa"].sum())
    resumo = pd.DataFrame(
        [{
            "total_licitacoes": total,
            "licitacoes_com_me_local": com_local,
            "licitacoes_sem_me_local": total - com_local,
            "percentual_me_local": com_local / total * 100,
            "licitacoes_com_me_externa": com_externa,
            "percentual_me_externa": com_externa / total * 100,
        }]
    )

    resultado: dict[str, object] = {"resumo_geral": resumo, "dados_tratados": dados}
    for nome, coluna in (("por_secretaria", coluna_secretaria), ("historico", coluna_data)):
        if not coluna:
            continue
        agrupador = coluna
        if nome == "historico":
            dados["_periodo"] = dados[coluna].astype("string").str.slice(0, 4).where(
                dados[coluna].astype("string").str.match(r"^\d{4}")
            )
            agrupador = "_periodo"
        por_licitacao = dados.groupby([agrupador, coluna_licitacao], dropna=False)["_me_local"].any().reset_index()
        tabela = por_licitacao.groupby(agrupador, dropna=False).agg(
            total_licitacoes=(coluna_licitacao, "nunique"),
            licitacoes_com_me_local=("_me_local", "sum"),
        ).reset_index()
        tabela["percentual_me_local"] = tabela["licitacoes_com_me_local"] / tabela["total_licitacoes"] * 100
        resultado[nome] = tabela

    comparacao = pd.DataFrame([
        {"tipo": "ME Local", "licitacoes": com_local, "percentual": com_local / total * 100},
        {"tipo": "ME Externa", "licitacoes": com_externa, "percentual": com_externa / total * 100},
    ])
    resultado["me_local_externa"] = comparacao
    return resultado


def extrair_ano_mes(coluna_data: pd.Series) -> pd.Series:
    """
    Extrai o periodo 'YYYY-MM' de uma coluna de data ja normalizada para ISO
    8601 por `app.utils.converter_datas` ('YYYY-MM-DD', 'YYYY-MM-DDTHH:MM:SS'
    ou '...Z'). Valores nulos/vazios (incluindo o marcador 'nao_informado' que
    `app.utils.tratar_nulos` usa em colunas de texto) viram `None`.
    """
    texto = coluna_data.astype("string")
    ano_mes = texto.str.slice(0, 7)
    valido = texto.notna() & texto.str.match(r"^\d{4}-\d{2}")
    return ano_mes.where(valido, None)


def calcular_participacao_me(
    df: pd.DataFrame,
    *,
    colunas_agrupamento: list[str],
    coluna_valor: str,
    coluna_elegivel_me: str = "fornecedor_elegivel_me",
) -> pd.DataFrame:
    """
    Agrupa por `colunas_agrupamento` e calcula, por grupo:
      - total_compras: soma de `coluna_valor` de todas as linhas do grupo
      - valor_me: soma de `coluna_valor` so onde `coluna_elegivel_me` e True
      - percentual_me: valor_me / total_compras (None quando o total e 0,
        para nao gerar divisao por zero nem confundir com "0% de participacao")

    Linhas sem fornecedor identificado (`coluna_elegivel_me` nulo — CNPJ nao
    encontrado na base de fornecedores, nao consultado, ou contratado pessoa
    fisica) contam para o total mas NAO para valor_me: sao "nao
    comprovadamente ME", o que e diferente de "comprovadamente nao ME"
    (`False`). EPP e MEI tambem nao contam neste KPI.
    """
    df = df.copy()
    eh_me = df[coluna_elegivel_me].map(lambda v: bool(v) if pd.notna(v) else False)
    df["_valor_me"] = df[coluna_valor].where(eh_me, 0)

    agrupado = (
        df.groupby(colunas_agrupamento, dropna=False)
        .agg(total_compras=(coluna_valor, "sum"), valor_me=("_valor_me", "sum"))
        .reset_index()
    )

    agrupado["percentual_me"] = (agrupado["valor_me"] / agrupado["total_compras"]).where(
        agrupado["total_compras"] != 0
    )

    return agrupado


def calcular_participacao_me_por_mes(
    df: pd.DataFrame,
    *,
    coluna_data: str,
    coluna_valor: str,
    coluna_elegivel_me: str = "fornecedor_elegivel_me",
) -> pd.DataFrame:
    """Atalho de `calcular_participacao_me` agrupando por mes (`extrair_ano_mes`)."""
    df = df.copy()
    df["ano_mes"] = extrair_ano_mes(df[coluna_data])
    return calcular_participacao_me(
        df,
        colunas_agrupamento=["ano_mes"],
        coluna_valor=coluna_valor,
        coluna_elegivel_me=coluna_elegivel_me,
    )

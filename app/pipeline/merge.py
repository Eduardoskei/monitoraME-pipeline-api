from __future__ import annotations

import pandas as pd

from app.pipeline.enrichment.fornecedores import (
    enriquecer_com_fornecedor,
    extrair_cnpjs_distintos,
)
from app.pipeline.enrichment.municipios import (
    buscar_municipios_uf,
    enriquecer_com_municipio,
    validar_e_enriquecer_municipio,
)
from app.pipeline.enrichment.origem_geografica import (
    NIVEL_FORA_DO_ESTADO,
    NIVEL_MESMO_MUNICIPIO,
    NIVEL_OUTRO_MUNICIPIO_CE,
    classificar_dataframe,
    classificar_origem_geografica,
)


def juntar_contratos_e_contratados(
    df_contratos: pd.DataFrame | None,
    df_contratados: pd.DataFrame | None,
) -> pd.DataFrame:
    """
    O endpoint 'contratos' do TCE-CE NAO traz o CNPJ/CPF do contratado —
    confirmei isso consultando a API ao vivo (api-dados-abertos.tce.ce.gov.br/sim):
    'contratos' tem valor/vigencia/objeto, mas o documento e o nome do
    contratado (`numero_documento_negociante`/`nome_negociante`) estao no
    endpoint separado 'contratados'. Os dois se ligam por
    ('numero_contrato', 'codigo_municipio').

    Faz o left join entre as duas tabelas ja limpas por `cleaners.tce.limpar`.
    Se `df_contratados` nao for informado (ou faltar a chave em algum dos
    lados), devolve `df_contratos` sem alteracao — nao inventa a chave.
    """
    if df_contratos is None or df_contratos.empty:
        return pd.DataFrame()
    if df_contratados is None or df_contratados.empty:
        return df_contratos.copy()

    chaves = ("numero_contrato", "codigo_municipio")
    if not all(chave in df_contratos.columns and chave in df_contratados.columns for chave in chaves):
        # falta uma das duas colunas de algum lado -> nao junta por chave parcial
        # (numero_contrato sozinho pode colidir entre municipios diferentes)
        return df_contratos.copy()

    colunas_contratados = list(chaves) + [c for c in df_contratados.columns if c not in chaves]
    return df_contratos.merge(
        df_contratados[colunas_contratados],
        on=list(chaves),
        how="left",
        suffixes=("", "_contratado"),
    )


def montar_base_tce(
    df_contratos: pd.DataFrame,
    df_contratados: pd.DataFrame | None = None,
    *,
    fornecedores_df: pd.DataFrame | None = None,
    coluna_cnpj_contratado: str = "numero_documento_negociante",
) -> pd.DataFrame:
    """
    Junta 'contratos' com 'contratados' (via `juntar_contratos_e_contratados`,
    necessario porque 'contratos' sozinho nao tem o CNPJ/CPF do contratado) e,
    quando `fornecedores_df` for informado, enriquece com os dados do
    fornecedor via `coluna_cnpj_contratado` ('numero_documento_negociante' —
    pode ser CNPJ ou CPF; `enriquecer_com_fornecedor` so casa quando for CNPJ
    de 14 digitos, entao um contratado pessoa fisica so fica sem
    enriquecimento, o que e o comportamento correto: a base de fornecedores
    so cobre CNPJ).

    NAO cruza com a base de municipios do IBGE: o 'codigo_municipio' do
    TCE-CE e um codigo INTERNO do proprio TCE (ex.: '010' para Amontada),
    numerado de forma diferente do codigo IBGE de 7 digitos.
    Existe uma tabela de correspondencia (`tce.buscar_municipios()` retorna
    'codigo_municipio' -> 'codigo_municipio_ibge', confirmado ao vivo), mas
    essa ligacao ainda nao esta implementada aqui.
    """
    df = juntar_contratos_e_contratados(df_contratos, df_contratados)
    if df.empty or fornecedores_df is None or coluna_cnpj_contratado not in df.columns:
        return df
    return enriquecer_com_fornecedor(df, fornecedores_df, coluna_cnpj=coluna_cnpj_contratado)


__all__ = [
    "NIVEL_FORA_DO_ESTADO",
    "NIVEL_MESMO_MUNICIPIO",
    "NIVEL_OUTRO_MUNICIPIO_CE",
    "buscar_municipios_uf",
    "classificar_dataframe",
    "classificar_origem_geografica",
    "enriquecer_com_fornecedor",
    "enriquecer_com_municipio",
    "extrair_cnpjs_distintos",
    "juntar_contratos_e_contratados",
    "montar_base_tce",
    "validar_e_enriquecer_municipio",
]

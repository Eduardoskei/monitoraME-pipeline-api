from __future__ import annotations

import pandas as pd

from app.utils import normalizar_cnpj


_COLUNAS_FORNECEDOR_EXPORTADAS = (
    "cnpj",
    "razao_social",
    "porte_padronizado",
    "municipio_sede",
    "uf_sede",
    "cnae_principal_codigo",
    "cnae_principal_descricao",
    "cnaes",
    "elegivel_me",
    "cnpj_valido",
    "opencnpj_status",
    "optante_simples_nacional",
    "data_opcao_simples_nacional",
    "data_exclusao_simples_nacional",
    "optante_mei",
    "data_opcao_mei",
)


def extrair_cnpjs_distintos(*colunas_cnpj: pd.Series | None) -> list[str]:
    """
    Recebe uma ou mais Series de CNPJ/documento e devolve CNPJs distintos e
    validos, prontos para consulta na fonte cadastral.
    """
    cnpjs: set[str] = set()
    for serie in colunas_cnpj:
        if serie is None:
            continue
        for valor in serie.dropna():
            cnpj = normalizar_cnpj(valor)
            if cnpj:
                cnpjs.add(cnpj)
    return sorted(cnpjs)


def enriquecer_com_fornecedor(
    df: pd.DataFrame,
    fornecedores_df: pd.DataFrame | None,
    *,
    coluna_cnpj: str,
) -> pd.DataFrame:
    """
    Faz left join dos dados cadastrais limpos em uma tabela que contenha CNPJ
    de fornecedor/contratado, prefixando os campos resultantes com
    ``fornecedor_``.
    """
    df = df.copy()
    if coluna_cnpj not in df.columns or fornecedores_df is None or fornecedores_df.empty:
        return df
    if "cnpj" not in fornecedores_df.columns:
        return df

    colunas = [c for c in _COLUNAS_FORNECEDOR_EXPORTADAS if c in fornecedores_df.columns]
    referencia = fornecedores_df[colunas].add_prefix("fornecedor_")
    chave_temp = "_cnpj_merge"
    referencia = referencia.rename(columns={"fornecedor_cnpj": chave_temp})
    referencia = referencia.drop_duplicates(subset=[chave_temp])

    df[chave_temp] = df[coluna_cnpj].map(normalizar_cnpj)
    resultado = df.merge(referencia, on=chave_temp, how="left")
    return resultado.drop(columns=[chave_temp])


__all__ = ["enriquecer_com_fornecedor", "extrair_cnpjs_distintos"]

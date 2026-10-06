from __future__ import annotations

import pandas as pd

from app.utils import normalizar_cnpj


_COLUNAS_FORNECEDOR_EXPORTADAS = (
    "cnpj",
    "cnpj_raiz",
    "razao_social",
    "porte_padronizado",
    "porte_procedencia",
    "mei_discriminado",
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

_COLUNAS_PORTE_POR_RAIZ = (
    "porte_padronizado",
    "porte_procedencia",
    "mei_discriminado",
    "elegivel_me",
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

    # O porte e atributo da empresa (raiz de 8 digitos), enquanto municipio,
    # UF e CNAE continuam pertencendo ao estabelecimento completo de 14.
    if "cnpj_raiz" in fornecedores_df.columns:
        colunas_raiz = [
            coluna
            for coluna in ("cnpj_raiz", *_COLUNAS_PORTE_POR_RAIZ)
            if coluna in fornecedores_df.columns
        ]
        referencia_raiz = fornecedores_df[colunas_raiz].copy()
        referencia_raiz = referencia_raiz.dropna(subset=["cnpj_raiz"])
        referencia_raiz = referencia_raiz.drop_duplicates(subset=["cnpj_raiz"])
        referencia_raiz = referencia_raiz.rename(
            columns={
                "cnpj_raiz": "_cnpj_raiz_merge",
                **{coluna: f"_raiz_{coluna}" for coluna in colunas_raiz if coluna != "cnpj_raiz"},
            }
        )
        resultado["_cnpj_raiz_merge"] = resultado[chave_temp].str.slice(0, 8)
        resultado = resultado.merge(referencia_raiz, on="_cnpj_raiz_merge", how="left")
        for coluna in _COLUNAS_PORTE_POR_RAIZ:
            coluna_raiz = f"_raiz_{coluna}"
            coluna_destino = f"fornecedor_{coluna}"
            if coluna_raiz not in resultado.columns:
                continue
            if coluna_destino in resultado.columns:
                resultado[coluna_destino] = resultado[coluna_raiz].combine_first(
                    resultado[coluna_destino]
                )
            else:
                resultado[coluna_destino] = resultado[coluna_raiz]
            resultado = resultado.drop(columns=[coluna_raiz])
        resultado = resultado.drop(columns=["_cnpj_raiz_merge"])

    return resultado.drop(columns=[chave_temp])


__all__ = ["enriquecer_com_fornecedor", "extrair_cnpjs_distintos"]

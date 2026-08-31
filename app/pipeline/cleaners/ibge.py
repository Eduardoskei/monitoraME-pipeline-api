"""Limpeza dos dados da API de Localidades do IBGE."""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.utils import limpar_generico


def limpar_municipios(registros: list[dict[str, Any]]) -> pd.DataFrame:
    """Limpa a lista de municipios do IBGE."""
    df = limpar_generico(
        registros,
        colunas_obrigatorias=["id"],
        chave_duplicata=["id"],
    )
    if "id" in df.columns:
        df["id"] = df["id"].astype("Int64")
    return df


def validar_municipio_uf(
    df: pd.DataFrame,
    municipios_ibge: pd.DataFrame,
    *,
    coluna_codigo_municipio: str,
    coluna_uf: str,
) -> pd.DataFrame:
    """
    Cruza o codigo de municipio (IBGE) informado por uma fonte contra a base
    oficial ja limpa e sinaliza se a UF informada bate com a UF oficial.
    """
    df = df.copy()
    coluna_resultado = f"{coluna_codigo_municipio}_uf_confere"

    if coluna_codigo_municipio not in df.columns or coluna_uf not in df.columns:
        return df
    if "id" not in municipios_ibge.columns or "microrregiao_mesorregiao_uf_sigla" not in municipios_ibge.columns:
        return df

    referencia = municipios_ibge.dropna(subset=["id"]).set_index("id")["microrregiao_mesorregiao_uf_sigla"]

    codigo_numerico = pd.to_numeric(df[coluna_codigo_municipio], errors="coerce").astype("Int64")
    uf_oficial = codigo_numerico.map(referencia)

    nao_verificavel = uf_oficial.isna() | df[coluna_uf].isna()
    confere = uf_oficial.astype(str).str.strip().str.upper() == df[coluna_uf].astype(str).str.strip().str.upper()

    df[coluna_resultado] = confere.where(~nao_verificavel, None)
    return df

__all__ = ["limpar_municipios", "validar_municipio_uf"]

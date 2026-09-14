from __future__ import annotations

import pandas as pd
import requests

from app.pipeline.cleaners.ibge import validar_municipio_uf


def enriquecer_com_municipio(
    df: pd.DataFrame,
    municipios_ibge: pd.DataFrame | None,
    *,
    coluna_codigo_municipio: str,
) -> pd.DataFrame:
    """
    Faz left join com a base de municipios do IBGE ja limpa, trazendo nome e UF
    oficiais para exibicao e validacao.
    """
    df = df.copy()
    if coluna_codigo_municipio not in df.columns or municipios_ibge is None or municipios_ibge.empty:
        return df
    if "id" not in municipios_ibge.columns:
        return df

    colunas = [c for c in ("id", "nome", "microrregiao_mesorregiao_uf_sigla") if c in municipios_ibge.columns]
    referencia = municipios_ibge[colunas].rename(
        columns={"nome": "municipio_nome", "microrregiao_mesorregiao_uf_sigla": "municipio_uf"}
    )

    chave_temp = "_codigo_municipio_merge"
    df[chave_temp] = pd.to_numeric(df[coluna_codigo_municipio], errors="coerce").astype("Int64")
    resultado = df.merge(referencia, left_on=chave_temp, right_on="id", how="left")
    return resultado.drop(columns=[chave_temp, "id"])


def validar_e_enriquecer_municipio(
    df: pd.DataFrame,
    municipios_ibge: pd.DataFrame | None,
    *,
    coluna_codigo_municipio: str,
    coluna_uf: str | None = None,
) -> pd.DataFrame:
    """
    Combina enriquecimento com municipio IBGE e validacao de divergencia de UF.
    """
    df = enriquecer_com_municipio(df, municipios_ibge, coluna_codigo_municipio=coluna_codigo_municipio)
    if coluna_uf and municipios_ibge is not None and not municipios_ibge.empty:
        df = validar_municipio_uf(
            df,
            municipios_ibge,
            coluna_codigo_municipio=coluna_codigo_municipio,
            coluna_uf=coluna_uf,
        )
    return df


def buscar_municipios_uf(uf: str = "CE") -> list[str]:
    """
    Busca na API de Localidades do IBGE os nomes dos municipios de uma UF.
    """
    url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf.upper()}/municipios"
    resposta = requests.get(url, timeout=10)
    resposta.raise_for_status()
    dados = resposta.json()
    return sorted({item["nome"] for item in dados if "nome" in item})


__all__ = [
    "buscar_municipios_uf",
    "enriquecer_com_municipio",
    "validar_e_enriquecer_municipio",
]

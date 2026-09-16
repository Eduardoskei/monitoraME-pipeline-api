"""Limpeza dos dados cadastrais consultados na API OpenCNPJ."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from app.utils import limpar_generico, primeira_coluna_preenchida, remover_acentos, somente_digitos

_MAPA_PORTE_EMPRESARIAL = {
    "MEI": "MEI",
    "MICROEMPREENDEDOR INDIVIDUAL": "MEI",
    "ME": "ME",
    "MICRO EMPRESA": "ME",
    "MICROEMPRESA": "ME",
    "EPP": "EPP",
    "EMPRESA DE PEQUENO PORTE": "EPP",
    "DEMAIS": "DEMAIS",
    "OUTROS": "DEMAIS",
    "NAO INFORMADO": "DEMAIS",
    "1": "MEI",
    "2": "ME",
    "3": "EPP",
    "5": "DEMAIS",
}

_MARCADORES_AUSENTES = ("", "nao_informado", "NAO INFORMADO")

_COLUNAS_MUNICIPIO_SEDE = (
    "municipio",
    "endereco_municipio",
    "opencnpj_municipio",
    "opencnpj_endereco_municipio",
)

_COLUNAS_UF_SEDE = (
    "uf",
    "endereco_uf",
    "opencnpj_uf",
    "opencnpj_endereco_uf",
)

_COLUNAS_CNAE_PRINCIPAL_CODIGO = (
    "cnae_principal",
    "opencnpj_cnae_principal",
)

_COLUNAS_CNAE_PRINCIPAL_DESCRICAO = (
    "cnae_principal_descricao",
    "opencnpj_cnae_principal_descricao",
)

_COLUNAS_CNAES = (
    "cnaes_secundarios",
    "opencnpj_cnaes_secundarios",
)


def normalizar_porte_empresarial(valor: Any) -> str | None:
    """
    Mapeia variacoes de porte cadastral para MEI/ME/EPP/DEMAIS.

    Retorna None para valores desconhecidos, sem inferir uma categoria.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    chave = remover_acentos(str(valor)).strip().upper()
    chave = re.sub(r"\s+", " ", chave)
    if chave == "":
        return None
    return _MAPA_PORTE_EMPRESARIAL.get(chave)


def normalizar_booleano(valor: Any) -> Any:
    """Converte booleanos vindos como bool/int/string e preserva ausentes."""
    if valor is None:
        return pd.NA
    try:
        if pd.isna(valor):
            return pd.NA
    except (TypeError, ValueError):
        pass
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)) and valor in (0, 1):
        return bool(valor)

    chave = remover_acentos(str(valor)).strip().upper()
    chave = re.sub(r"[\s_-]+", " ", chave)
    if chave in {"TRUE", "T", "SIM", "S", "YES", "Y", "1"}:
        return True
    if chave in {"FALSE", "F", "NAO", "N", "NO", "0"}:
        return False
    return pd.NA


def normalizar_uf(valor: Any) -> str | None:
    """Normaliza sigla de UF preservando ausentes como nulos."""
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    texto = str(valor).strip().upper()
    texto_sem_acento = remover_acentos(texto).replace("_", " ")
    if texto == "" or texto_sem_acento == "NAO INFORMADO":
        return None
    return texto if len(texto) == 2 else None


def normalizar_codigo_cnae(valor: Any) -> str | None:
    """Mantem apenas os digitos do CNAE principal sem inferir atividade."""
    digitos = somente_digitos(valor)
    return digitos or None


def normalizar_lista_cnaes(valor: Any) -> Any:
    """Preserva listas de CNAEs e reidrata listas simples achatadas pelo cleaner generico."""
    if valor is None:
        return pd.NA
    try:
        if pd.isna(valor):
            return pd.NA
    except (TypeError, ValueError):
        pass
    if isinstance(valor, list):
        return valor
    if isinstance(valor, str):
        partes = [parte.strip() for parte in valor.split(";") if parte.strip()]
        codigos = [normalizar_codigo_cnae(parte) for parte in partes]
        return [codigo for codigo in codigos if codigo]
    return valor


def _campo_canonico(df: pd.DataFrame, colunas: tuple[str, ...]) -> pd.Series:
    return primeira_coluna_preenchida(df, colunas, marcadores_vazios=_MARCADORES_AUSENTES)


def limpar_fornecedores(registros: list[dict[str, Any]]) -> pd.DataFrame:
    """
    Limpa o retorno de OpenCNPJ preservado pelo dominio de fornecedores.

    Alem do pipeline generico, adiciona porte padronizado, elegibilidade ME e
    flags/datas do Simples Nacional e MEI quando os campos estiverem presentes.
    """
    df = limpar_generico(
        registros,
        colunas_data=None,
        colunas_obrigatorias=["cnpj"],
        chave_duplicata=["cnpj"],
    )
    if df.empty:
        return df

    colunas_porte = [
        c
        for c in (
            "porte",
            "porte_empresa",
            "opencnpj_porte_empresa",
        )
        if c in df.columns
    ]
    if colunas_porte:
        porte = df[colunas_porte[0]]
        for coluna in colunas_porte[1:]:
            porte = porte.combine_first(df[coluna])
        df["porte_padronizado"] = porte.map(normalizar_porte_empresarial)
        elegivel = df["porte_padronizado"].eq("ME").astype("boolean")
        df["elegivel_me"] = elegivel.mask(df["porte_padronizado"].isna(), pd.NA)
    else:
        df["porte_padronizado"] = pd.Series([pd.NA] * len(df), dtype="string")
        df["elegivel_me"] = pd.Series([pd.NA] * len(df), dtype="boolean")

    coluna_simples = next(
        (
            c
            for c in (
                "opcao_simples",
                "simples_mei_opcao_simples",
                "opencnpj_opcao_simples",
                "opencnpj_simples_mei_opcao_simples",
            )
            if c in df.columns
        ),
        None,
    )
    if coluna_simples:
        df["optante_simples_nacional"] = df[coluna_simples].map(normalizar_booleano).astype("boolean")

    coluna_data_opcao_simples = next(
        (
            c
            for c in (
                "data_opcao_simples",
                "simples_mei_data_opcao_simples",
                "opencnpj_data_opcao_simples",
                "opencnpj_simples_mei_data_opcao_simples",
            )
            if c in df.columns
        ),
        None,
    )
    if coluna_data_opcao_simples:
        df["data_opcao_simples_nacional"] = df[coluna_data_opcao_simples]

    coluna_data_exclusao_simples = next(
        (
            c
            for c in (
                "data_exclusao_simples",
                "simples_mei_data_exclusao_simples",
                "opencnpj_data_exclusao_simples",
                "opencnpj_simples_mei_data_exclusao_simples",
            )
            if c in df.columns
        ),
        None,
    )
    if coluna_data_exclusao_simples:
        df["data_exclusao_simples_nacional"] = df[coluna_data_exclusao_simples]

    coluna_mei = next(
        (
            c
            for c in (
                "opcao_mei",
                "simples_mei_opcao_mei",
                "opencnpj_opcao_mei",
                "opencnpj_simples_mei_opcao_mei",
            )
            if c in df.columns
        ),
        None,
    )
    if coluna_mei:
        df["optante_mei"] = df[coluna_mei].map(normalizar_booleano).astype("boolean")

    coluna_data_opcao_mei = next(
        (
            c
            for c in (
                "data_opcao_mei",
                "simples_mei_data_opcao_mei",
                "opencnpj_data_opcao_mei",
                "opencnpj_simples_mei_data_opcao_mei",
            )
            if c in df.columns
        ),
        None,
    )
    if coluna_data_opcao_mei:
        df["data_opcao_mei"] = df[coluna_data_opcao_mei]

    df["municipio_sede"] = _campo_canonico(df, _COLUNAS_MUNICIPIO_SEDE)
    df["uf_sede"] = _campo_canonico(df, _COLUNAS_UF_SEDE).map(normalizar_uf)
    df["cnae_principal_codigo"] = _campo_canonico(df, _COLUNAS_CNAE_PRINCIPAL_CODIGO).map(normalizar_codigo_cnae)
    df["cnae_principal_descricao"] = _campo_canonico(df, _COLUNAS_CNAE_PRINCIPAL_DESCRICAO)
    df["cnaes"] = _campo_canonico(df, _COLUNAS_CNAES).map(normalizar_lista_cnaes)

    return df


__all__ = [
    "limpar_fornecedores",
    "normalizar_booleano",
    "normalizar_codigo_cnae",
    "normalizar_lista_cnaes",
    "normalizar_porte_empresarial",
    "normalizar_uf",
]

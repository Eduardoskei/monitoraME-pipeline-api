"""Limpeza dos dados da API PNCP."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

import pandas as pd

from app.utils import (
    achatar_lista_para_tabela,
    achatar_registros,
    converter_datas,
    converter_numericos,
    padronizar_chaves_entidades,
    padronizar_documentos,
    padronizar_nomes_colunas,
    padronizar_textos,
    remover_acentos,
    remover_duplicatas,
    tratar_nulos,
)


_MAPA_PORTE_PNCP = {
    "1": "ME",
    "2": "EPP",
    "3": "DEMAIS",
    "4": "NAO_SE_APLICA",
    "5": "NAO_INFORMADO",
}
_PORTES_PNCP = set(_MAPA_PORTE_PNCP.values())


def normalizar_porte_pncp(valor: Any) -> str | None:
    """Converte o codigo/descricao de porte do PNCP para um vocabulario fixo."""
    if valor is None:
        return None

    try:
        if bool(pd.isna(valor)):
            return None
    except (TypeError, ValueError):
        pass

    texto = remover_acentos(str(valor)).strip().upper()
    if not texto:
        return None

    descricao = re.sub(r"[\s-]+", "_", texto)
    if descricao in _PORTES_PNCP:
        return descricao

    try:
        numero = Decimal(texto)
    except InvalidOperation:
        return None

    if not numero.is_finite() or numero != numero.to_integral_value():
        return None

    return _MAPA_PORTE_PNCP.get(str(int(numero)))


def padronizar_porte_pncp(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona a classificacao padronizada sem alterar a coluna original."""
    df = df.copy()
    if "porte_fornecedor_id" not in df.columns:
        return df

    df["porte_fornecedor_padronizado"] = (
        df["porte_fornecedor_id"].map(normalizar_porte_pncp).astype("string")
    )
    return df


def _limpar_tabela_filha(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica a limpeza comum usada nas tabelas filhas do PNCP."""
    df = padronizar_textos(df)
    df = converter_numericos(df)
    df = converter_datas(df)
    df = padronizar_documentos(df)
    df = padronizar_chaves_entidades(df)
    return remover_duplicatas(df)


def limpar_contratacoes(registros: list[dict[str, Any]]) -> dict[str, pd.DataFrame]:
    """
    Limpa contratacoes do PNCP.

    Retorna um dict de tabelas:
    - 'contratacoes': 1 linha por compra/contratacao;
    - 'itens'/'resultados'/'contratos': tabelas filhas, se presentes.
    """
    colunas_data = [
        "data_abertura_proposta",
        "data_encerramento_proposta",
        "data_publicacao_pncp",
        "data_inclusao",
        "data_atualizacao",
    ]
    colunas_numericas = ["valor_total_estimado", "valor_total_homologado"]
    colunas_obrigatorias = ["numero_controle_pncp"]

    df = achatar_registros(registros)
    if df.empty:
        return {"contratacoes": df}

    df = padronizar_nomes_colunas(df)
    df = padronizar_textos(df)
    df = converter_numericos(df, [c for c in colunas_numericas if c in df.columns])
    df = converter_datas(df, [c for c in colunas_data if c in df.columns])
    df = padronizar_documentos(df)
    df = padronizar_chaves_entidades(df)

    chave_obrigatoria = [c for c in colunas_obrigatorias if c in df.columns]
    df = tratar_nulos(df, colunas_obrigatorias=chave_obrigatoria or None)
    df = remover_duplicatas(df, subset=chave_obrigatoria or None)

    tabelas = {"contratacoes": df}

    chave_pai = chave_obrigatoria[0] if chave_obrigatoria else df.columns[0]
    for coluna_lista in ("itens", "contratos"):
        filha = achatar_lista_para_tabela(df, coluna_lista, chave_pai)
        if filha is not None:
            if coluna_lista == "itens":
                chaves_resultado = [chave_pai]
                if "numero_item" in filha.columns:
                    chaves_resultado.append("numero_item")
                resultados = achatar_lista_para_tabela(filha, "resultados", chaves_resultado)
                if resultados is not None:
                    resultados = _limpar_tabela_filha(resultados)
                    tabelas["resultados"] = padronizar_porte_pncp(resultados)
                filha = filha.drop(columns=["resultados"], errors="ignore")

            tabelas[coluna_lista] = _limpar_tabela_filha(filha)
            df = df.drop(columns=[coluna_lista])

    tabelas["contratacoes"] = df
    return tabelas


__all__ = [
    "limpar_contratacoes",
    "normalizar_porte_pncp",
    "padronizar_porte_pncp",
]

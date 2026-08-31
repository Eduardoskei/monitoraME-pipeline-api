"""Limpeza dos dados da API PNCP."""

from __future__ import annotations

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
    remover_duplicatas,
    tratar_nulos,
)


def limpar_contratacoes(registros: list[dict[str, Any]]) -> dict[str, pd.DataFrame]:
    """
    Limpa contratacoes do PNCP.

    Retorna um dict de tabelas:
    - 'contratacoes': 1 linha por compra/contratacao;
    - 'itens'/'contratos': tabelas filhas, se presentes nos registros.
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
            filha = padronizar_textos(filha)
            filha = converter_numericos(filha)
            filha = converter_datas(filha)
            filha = padronizar_documentos(filha)
            filha = padronizar_chaves_entidades(filha)
            filha = remover_duplicatas(filha)
            tabelas[coluna_lista] = filha
            df = df.drop(columns=[coluna_lista])

    tabelas["contratacoes"] = df
    return tabelas

__all__ = ["limpar_contratacoes"]

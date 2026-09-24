from __future__ import annotations

from typing import Any

import pandas as pd

from app.pipeline.naturezas_despesa import (
    descricao_natureza_despesa,
    normalizar_codigo_natureza_despesa,
)
from app.utils import normalizar_cnpj, valor_preenchido


BASE_CALCULO_CONTRATOS_TCE = "contratos_tce"
ORIGEM_NAO_IDENTIFICADA = "Nao identificada"
PORTE_NAO_IDENTIFICADO = "NAO_IDENTIFICADO"

COLUNAS_BASE_ANALITICA_TCE = (
    "fonte",
    "base_calculo",
    "codigo_municipio_tce",
    "municipio_comprador",
    "numero_contrato",
    "data_referencia",
    "ano",
    "ano_mes",
    "natureza_despesa_codigo",
    "natureza_despesa",
    "valor",
    "cnpj_fornecedor",
    "nome_fornecedor",
    "porte_fornecedor",
    "fornecedor_e_me",
    "fornecedor_e_mpe",
    "municipio_sede_fornecedor",
    "uf_sede_fornecedor",
    "cnae_principal_fornecedor",
    "cnae_principal_descricao_fornecedor",
    "origem_geografica",
)


def _serie_ou_padrao(df: pd.DataFrame, coluna: str, padrao: Any = pd.NA) -> pd.Series:
    if coluna in df.columns:
        return df[coluna]
    return pd.Series([padrao] * len(df), index=df.index, dtype=object)


def _primeira_coluna(df: pd.DataFrame, colunas: tuple[str, ...], padrao: Any = pd.NA) -> pd.Series:
    resultado = pd.Series([padrao] * len(df), index=df.index, dtype=object)
    preenchido = pd.Series([False] * len(df), index=df.index)

    for coluna in colunas:
        if coluna not in df.columns:
            continue
        serie = df[coluna].astype(object)
        usar = ~preenchido & serie.map(valor_preenchido)
        resultado.loc[usar] = serie.loc[usar]
        preenchido.loc[usar] = True

    return resultado


def _texto_ou_na(valor: Any) -> str | None:
    if not valor_preenchido(valor):
        return None
    texto = str(valor).strip()
    return texto or None


def _normalizar_cnpj_fornecedor(valor: Any) -> str | None:
    cnpj = normalizar_cnpj(valor)
    return cnpj if cnpj is not None and len(cnpj) == 14 else None


def _ano_mes(data: pd.Series) -> pd.Series:
    texto = data.astype("string")
    ano_mes = texto.str.slice(0, 7)
    valido = texto.notna() & texto.str.match(r"^\d{4}-\d{2}")
    return ano_mes.where(valido, None)


def _ano(data: pd.Series) -> pd.Series:
    texto = data.astype("string")
    ano = texto.str.slice(0, 4)
    valido = texto.notna() & texto.str.match(r"^\d{4}")
    return ano.where(valido, None)


def _natureza_codigo(df: pd.DataFrame) -> pd.Series:
    origem = _primeira_coluna(
        df,
        (
            "codigo_elemento_despesa",
            "natureza_despesa_codigo",
            "codigo_natureza_despesa",
        ),
    )
    return origem.map(normalizar_codigo_natureza_despesa).astype(object)


def _natureza_descricao(df: pd.DataFrame, codigos: pd.Series) -> pd.Series:
    descricoes = codigos.map(descricao_natureza_despesa).astype(object)
    fallback = _primeira_coluna(
        df,
        (
            "natureza_despesa",
            "descricao_elemento_despesa",
            "descricao_natureza_despesa",
        ),
    )
    return descricoes.combine_first(fallback)


def _classificar_origem(
    municipio_comprador: Any,
    uf_fornecedor: Any,
    municipio_fornecedor: Any,
    uf_comprador: str,
) -> str:
    uf_forn = _texto_ou_na(uf_fornecedor)
    if uf_forn is None:
        return ORIGEM_NAO_IDENTIFICADA

    if uf_forn.upper() != uf_comprador.upper():
        return "Fora do estado"

    municipio_comp = _texto_ou_na(municipio_comprador)
    municipio_forn = _texto_ou_na(municipio_fornecedor)
    if municipio_comp is None or municipio_forn is None:
        return ORIGEM_NAO_IDENTIFICADA

    from app.pipeline.enrichment.origem_geografica import classificar_origem_geografica

    return classificar_origem_geografica(
        municipio_comp,
        uf_forn,
        municipio_forn,
        uf_comprador,
    )


def montar_base_analitica_tce(
    base_tce: pd.DataFrame,
    *,
    codigo_municipio: str | None = None,
    municipio_comprador: str | None = None,
    uf_comprador: str = "CE",
) -> pd.DataFrame:
    """Monta a base canonica usada pelos indicadores analiticos do TCE.

    A entrada esperada e a base ja limpa/enriquecida por
    ``analisys.montar_base_tce_contratos``. Enquanto a fonte de empenhos nao
    estiver definida, ``base_calculo`` deixa explicito que o grao atual vem de
    contratos do TCE.
    """
    if base_tce is None or base_tce.empty:
        return pd.DataFrame(columns=COLUNAS_BASE_ANALITICA_TCE)

    base = base_tce.copy()
    resultado = pd.DataFrame(index=base.index)

    resultado["fonte"] = "TCE-CE"
    resultado["base_calculo"] = BASE_CALCULO_CONTRATOS_TCE
    resultado["codigo_municipio_tce"] = _primeira_coluna(
        base,
        ("codigo_municipio",),
        padrao=codigo_municipio,
    )
    if codigo_municipio is not None:
        resultado["codigo_municipio_tce"] = resultado["codigo_municipio_tce"].fillna(codigo_municipio)

    resultado["municipio_comprador"] = _primeira_coluna(
        base,
        ("municipio_comprador", "nome_municipio", "municipio_nome"),
        padrao=municipio_comprador,
    )
    if municipio_comprador is not None:
        resultado["municipio_comprador"] = resultado["municipio_comprador"].fillna(municipio_comprador)

    resultado["numero_contrato"] = _serie_ou_padrao(base, "numero_contrato")
    resultado["data_referencia"] = _primeira_coluna(base, ("data_contrato", "data_referencia"))
    resultado["ano"] = _ano(resultado["data_referencia"])
    resultado["ano_mes"] = _ano_mes(resultado["data_referencia"])

    codigos_natureza = _natureza_codigo(base)
    resultado["natureza_despesa_codigo"] = codigos_natureza
    resultado["natureza_despesa"] = _natureza_descricao(base, codigos_natureza)

    resultado["valor"] = pd.to_numeric(
        _primeira_coluna(base, ("valor_total_contrato", "valor", "valor_empenhado")),
        errors="coerce",
    )

    documento_fornecedor = _primeira_coluna(
        base,
        ("numero_documento_negociante", "cnpj_fornecedor", "ni_fornecedor"),
    )
    resultado["cnpj_fornecedor"] = documento_fornecedor.map(_normalizar_cnpj_fornecedor)
    resultado["nome_fornecedor"] = _primeira_coluna(
        base,
        ("nome_negociante", "nome_fornecedor", "nome_razao_social_fornecedor"),
    )

    porte = _primeira_coluna(base, ("fornecedor_porte_padronizado", "porte_fornecedor"))
    resultado["porte_fornecedor"] = porte.where(porte.map(valor_preenchido), PORTE_NAO_IDENTIFICADO)
    resultado["fornecedor_e_me"] = resultado["porte_fornecedor"].eq("ME")
    resultado["fornecedor_e_mpe"] = resultado["porte_fornecedor"].isin(("ME", "EPP"))

    resultado["municipio_sede_fornecedor"] = _primeira_coluna(
        base,
        ("fornecedor_municipio_sede", "municipio_sede_fornecedor"),
    )
    resultado["uf_sede_fornecedor"] = _primeira_coluna(
        base,
        ("fornecedor_uf_sede", "uf_sede_fornecedor"),
    )
    resultado["cnae_principal_fornecedor"] = _primeira_coluna(
        base,
        ("fornecedor_cnae_principal_codigo", "cnae_principal_fornecedor"),
    )
    resultado["cnae_principal_descricao_fornecedor"] = _primeira_coluna(
        base,
        ("fornecedor_cnae_principal_descricao", "cnae_principal_descricao_fornecedor"),
    )

    resultado["origem_geografica"] = resultado.apply(
        lambda linha: _classificar_origem(
            linha["municipio_comprador"],
            linha["uf_sede_fornecedor"],
            linha["municipio_sede_fornecedor"],
            uf_comprador,
        ),
        axis=1,
    )

    return resultado.loc[:, list(COLUNAS_BASE_ANALITICA_TCE)].reset_index(drop=True)


__all__ = [
    "BASE_CALCULO_CONTRATOS_TCE",
    "COLUNAS_BASE_ANALITICA_TCE",
    "ORIGEM_NAO_IDENTIFICADA",
    "PORTE_NAO_IDENTIFICADO",
    "montar_base_analitica_tce",
]

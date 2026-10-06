"""Pre-agregacao vetorizada de indicadores para multiplos municipios."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


TOTAL_MUNICIPIOS_CEARA = 184
TOTAL_INDICADORES_CARGA = 47
TOTAL_RESULTADOS_CARGA = TOTAL_MUNICIPIOS_CEARA * TOTAL_INDICADORES_CARGA


class ConfiguracaoApuracaoMunicipalError(ValueError):
    """A massa ou o catalogo de indicadores nao permite uma apuracao segura."""


def _validar_colunas(df: pd.DataFrame, colunas: Sequence[str], origem: str) -> None:
    ausentes = [coluna for coluna in colunas if coluna not in df.columns]
    if ausentes:
        raise ConfiguracaoApuracaoMunicipalError(
            f"{origem}: campos necessarios nao existem: {', '.join(ausentes)}"
        )


def _normalizar_dimensao_municipal(
    df: pd.DataFrame,
    *,
    coluna_codigo: str,
    coluna_nome: str,
) -> pd.DataFrame:
    dimensao = df[[coluna_codigo, coluna_nome]].copy()
    dimensao[coluna_codigo] = dimensao[coluna_codigo].astype("string").str.strip()
    dimensao[coluna_nome] = dimensao[coluna_nome].astype("string").str.strip()
    dimensao = dimensao.dropna(subset=[coluna_codigo])
    dimensao = dimensao[dimensao[coluna_codigo].ne("")]

    duplicados = dimensao[coluna_codigo].duplicated(keep=False)
    if duplicados.any():
        codigos = sorted(dimensao.loc[duplicados, coluna_codigo].unique().tolist())
        raise ConfiguracaoApuracaoMunicipalError(
            "dimensao municipal possui codigos duplicados: " + ", ".join(codigos[:5])
        )
    return dimensao.reset_index(drop=True)


def apurar_indicadores_por_municipio(
    base: pd.DataFrame,
    *,
    colunas_indicadores: Sequence[str],
    municipios: pd.DataFrame | None = None,
    coluna_codigo: str = "codigo_municipio_tce",
    coluna_nome: str = "municipio_comprador",
) -> pd.DataFrame:
    """Soma contribuicoes de indicadores para todos os municipios de uma vez.

    ``base`` deve estar no grao do fato (por exemplo, contrato) e carregar uma
    coluna numerica por indicador. O retorno usa formato longo, com uma linha
    por municipio/indicador, e indice composto unico para consultas rapidas.
    Quando ``municipios`` e informado, municipios sem fatos sao preservados
    com valor zero.
    """
    indicadores = list(colunas_indicadores)
    if not indicadores:
        raise ConfiguracaoApuracaoMunicipalError("informe ao menos um indicador")
    if len(set(indicadores)) != len(indicadores):
        raise ConfiguracaoApuracaoMunicipalError("o catalogo contem indicadores duplicados")

    _validar_colunas(base, [coluna_codigo, coluna_nome, *indicadores], "base")

    fatos = base[[coluna_codigo, coluna_nome, *indicadores]].copy()
    fatos[coluna_codigo] = fatos[coluna_codigo].astype("string").str.strip()
    fatos[coluna_nome] = fatos[coluna_nome].astype("string").str.strip()
    fatos = fatos.dropna(subset=[coluna_codigo])
    fatos = fatos[fatos[coluna_codigo].ne("")]
    fatos[indicadores] = fatos[indicadores].apply(pd.to_numeric, errors="coerce").fillna(0)

    agregado = (
        fatos.groupby(coluna_codigo, sort=False, observed=True)[indicadores]
        .sum()
        .reset_index()
    )
    nomes = (
        fatos.dropna(subset=[coluna_nome])
        .drop_duplicates(subset=[coluna_codigo])[[coluna_codigo, coluna_nome]]
    )
    agregado = agregado.merge(nomes, on=coluna_codigo, how="left")

    if municipios is not None:
        _validar_colunas(municipios, [coluna_codigo, coluna_nome], "municipios")
        dimensao = _normalizar_dimensao_municipal(
            municipios,
            coluna_codigo=coluna_codigo,
            coluna_nome=coluna_nome,
        )
        agregado = dimensao.merge(
            agregado.drop(columns=[coluna_nome]),
            on=coluna_codigo,
            how="left",
        )

    agregado[indicadores] = agregado[indicadores].fillna(0)
    resultado = agregado.melt(
        id_vars=[coluna_codigo, coluna_nome],
        value_vars=indicadores,
        var_name="indicador",
        value_name="valor",
    )
    resultado = resultado.sort_values([coluna_codigo, "indicador"]).reset_index(drop=True)
    resultado.index = pd.MultiIndex.from_frame(
        resultado[[coluna_codigo, "indicador"]],
        names=["municipio_idx", "indicador_idx"],
    )
    resultado.attrs.update(
        {
            "municipios": int(resultado[coluna_codigo].nunique()),
            "indicadores": len(indicadores),
            "resultados": len(resultado),
        }
    )
    return resultado


__all__ = [
    "ConfiguracaoApuracaoMunicipalError",
    "TOTAL_INDICADORES_CARGA",
    "TOTAL_MUNICIPIOS_CEARA",
    "TOTAL_RESULTADOS_CARGA",
    "apurar_indicadores_por_municipio",
]

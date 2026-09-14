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


def juntar_itens_pncp(tabelas: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Achata o dict retornado por `cleaners.pncp.limpar_contratacoes` em uma
    unica tabela "contratacoes x itens" (left join por 'numero_controle_pncp'),
    para quando o consumo final precisa de 1 linha por item em vez de tabelas
    separadas. Contratacoes sem item (coleta sem `incluir_detalhes=True`)
    continuam presentes, com as colunas de item vazias.
    """
    contratacoes = tabelas.get("contratacoes")
    if contratacoes is None or contratacoes.empty:
        return pd.DataFrame()

    itens = tabelas.get("itens")
    if itens is None or itens.empty:
        return contratacoes.copy()

    chave = "numero_controle_pncp" if "numero_controle_pncp" in contratacoes.columns else contratacoes.columns[0]
    if chave not in itens.columns:
        return contratacoes.copy()

    return contratacoes.merge(itens, on=chave, how="left", suffixes=("", "_item"))


# ---------------------------------------------------------------------------
# 2. ORQUESTRACAO POR FONTE — pontos de entrada usados pelo restante da app
# ---------------------------------------------------------------------------


def montar_base_pncp(
    tabelas_pncp: dict[str, pd.DataFrame],
    *,
    fornecedores_df: pd.DataFrame | None = None,
    municipios_ibge: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Enriquece o dict retornado por `cleaners.pncp.limpar_contratacoes`:
      - 'contratacoes': valida/enriquece o municipio do orgao contra o IBGE
        (via 'unidade_orgao_codigo_ibge'/'unidade_orgao_uf_sigla'), quando
        `municipios_ibge` for informado;
      - 'contratos' (se existir): enriquece com os dados do fornecedor
        vencedor (porte, elegibilidade ME, razao social) via
        'ni_fornecedor', quando `fornecedores_df` for informado.

    Tabelas ausentes no dict de entrada (ex.: 'itens'/'contratos', quando a
    coleta nao incluiu detalhes) simplesmente nao aparecem no resultado.
    """
    tabelas = {nome: tabela.copy() for nome, tabela in tabelas_pncp.items()}

    contratacoes = tabelas.get("contratacoes")
    if contratacoes is not None and not contratacoes.empty and municipios_ibge is not None:
        coluna_codigo = "unidade_orgao_codigo_ibge"
        coluna_uf = "unidade_orgao_uf_sigla"
        if coluna_codigo in contratacoes.columns:
            tabelas["contratacoes"] = validar_e_enriquecer_municipio(
                contratacoes,
                municipios_ibge,
                coluna_codigo_municipio=coluna_codigo,
                coluna_uf=coluna_uf if coluna_uf in contratacoes.columns else None,
            )

    contratos = tabelas.get("contratos")
    if contratos is not None and not contratos.empty and fornecedores_df is not None:
        if "ni_fornecedor" in contratos.columns:
            tabelas["contratos"] = enriquecer_com_fornecedor(
                contratos, fornecedores_df, coluna_cnpj="ni_fornecedor"
            )

    return tabelas


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
    numerado de forma diferente do codigo IBGE de 7 digitos usado pelo PNCP.
    Existe uma tabela de correspondencia (`tce.buscar_municipios()` retorna
    'codigo_municipio' -> 'codigo_municipio_ibge', confirmado ao vivo), mas
    essa ligacao ainda nao esta implementada aqui.
    """
    df = juntar_contratos_e_contratados(df_contratos, df_contratados)
    if df.empty or fornecedores_df is None or coluna_cnpj_contratado not in df.columns:
        return df
    return enriquecer_com_fornecedor(df, fornecedores_df, coluna_cnpj=coluna_cnpj_contratado)


# ---------------------------------------------------------------------------
# 3. UNIAO PNCP + TCE — empilha as duas fontes mantendo TODOS os campos
# ---------------------------------------------------------------------------


def unir_pncp_e_tce(
    df_pncp: pd.DataFrame | None,
    df_tce: pd.DataFrame | None,
) -> pd.DataFrame:
    """
    Empilha (union vertical) uma tabela do PNCP e uma do TCE numa unica
    tabela, preservando TODAS as colunas dos dois lados — a uniao, nao a
    intersecao. Onde uma coluna so existe em um dos lados, as linhas do
    outro lado ficam com NaN nela; nenhuma coluna e descartada. Cada linha
    ganha 'fonte' ('PNCP' ou 'TCE') indicando de onde veio.

    Ideal para comparar tabelas de granularidade equivalente, ex.:
    `cleaners.pncp.limpar_contratacoes(...)["contratacoes"]` com `cleaners.tce.limpar(...)`,
    ou as versoes ja enriquecidas por `montar_base_pncp`/`montar_base_tce`
    (nesse caso, colunas de enriquecimento com o MESMO nome dos dois lados,
    como 'fornecedor_porte_padronizado', ficam alinhadas na mesma coluna —
    e a unica situacao em que faz sentido ter valor dos dois lados juntos).

    Limitacao importante: isso NAO reconhece se a mesma contratacao aparece
    nas duas fontes ao mesmo tempo (nao ha chave compartilhada confiavel
    entre PNCP e TCE — ver discussao sobre merge probabilistico/fuzzy
    matching). Cada linha continua pertencendo a uma unica fonte; somar
    valores das duas fontes juntas nesta tabela corre risco de dupla
    contagem se a mesma contratacao for reportada em ambas. Nao use para
    KPIs que exigem uma fonte da verdade unica.
    """
    partes = []
    if df_pncp is not None and not df_pncp.empty:
        parte = df_pncp.copy()
        parte["fonte"] = "PNCP"
        partes.append(parte)
    if df_tce is not None and not df_tce.empty:
        parte = df_tce.copy()
        parte["fonte"] = "TCE"
        partes.append(parte)

    if not partes:
        return pd.DataFrame()

    return pd.concat(partes, ignore_index=True, sort=False)


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
    "juntar_itens_pncp",
    "montar_base_pncp",
    "montar_base_tce",
    "unir_pncp_e_tce",
    "validar_e_enriquecer_municipio",
]

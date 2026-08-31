from __future__ import annotations

import pandas as pd

from app.pipeline.cleaners.ibge import validar_municipio_uf
from app.utils import normalizar_cnpj

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
# 2. FORNECEDORES — enriquece qualquer tabela que tenha uma coluna de CNPJ
#    do fornecedor/contratado (PNCP: 'ni_fornecedor'; TCE: 'numero_documento_negociante')
# ---------------------------------------------------------------------------

_COLUNAS_FORNECEDOR_EXPORTADAS = (
    "cnpj",
    "razao_social",
    "porte_padronizado",
    "elegivel_me",
    "cnpj_valido",
    "opencnpj_status",
    "optante_simples_nacional",
    "optante_mei",
)


def extrair_cnpjs_distintos(*colunas_cnpj: pd.Series | None) -> list[str]:
    """
    Recebe uma ou mais Series de CNPJ/documento (ex.: `df_contratados["numero_documento_negociante"]`,
    `df_contratos_pncp["ni_fornecedor"]`) e devolve a lista de CNPJs distintos e
    validos (14 digitos apos normalizar) encontrados em todas elas juntas —
    pronta para alimentar `fornecedores.coletar_fornecedores_em_lote`.

    Documentos com 11 digitos (CPF, contratado pessoa fisica) ou formato
    invalido (ex.: valores mascarados/criptografados que o TCE as vezes
    retorna para CPF — confirmado na varredura real) sao descartados
    silenciosamente: a base de fornecedores so cobre CNPJ (OpenCNPJ nao faz
    consulta de CPF).
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
    Left join dos dados ja limpos por `cleaners.opencnpj.limpar_fornecedores` em
    qualquer tabela que tenha uma coluna de CNPJ do fornecedor/contratado.
    Traz porte/elegibilidade ME e razao social com o prefixo
    'fornecedor_', para nao colidir com colunas da tabela de origem.

    Nao remove nem filtra nada: uma linha cujo CNPJ nao foi encontrado na
    base de fornecedores fica com essas colunas em branco (NaN) — sinaliza
    "fornecedor nao consultado/nao encontrado" em vez de sumir da analise.

    `coluna_cnpj` e normalizada internamente (so digitos) antes do join: o
    campo do PNCP que traz o CNPJ do fornecedor vencedor se chama
    'niFornecedor' (nao contem a palavra "cnpj"), entao ele nunca passa pela
    normalizacao automatica de `app.utils.padronizar_documentos` — sem
    normalizar aqui tambem, o join falharia silenciosamente sempre que a
    formatacao dos dois lados divergisse.
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


# ---------------------------------------------------------------------------
# 3. IBGE — enriquece/valida o municipio informado por outra fonte contra a
#    base oficial (usa cleaners.ibge.validar_municipio_uf para o cruzamento)
# ---------------------------------------------------------------------------


def enriquecer_com_municipio(
    df: pd.DataFrame,
    municipios_ibge: pd.DataFrame | None,
    *,
    coluna_codigo_municipio: str,
) -> pd.DataFrame:
    """
    Left join com a base de municipios do IBGE ja limpa (`cleaners.ibge.limpar_municipios`),
    trazendo o nome/UF oficiais do municipio ('municipio_nome'/'municipio_uf')
    a partir do codigo IBGE informado pela outra fonte.
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
    Combina `enriquecer_com_municipio` (traz nome/UF oficiais para exibicao)
    com `cleaners.ibge.validar_municipio_uf` (sinaliza divergencia entre a UF
    informada pela fonte e a UF oficial do municipio), quando `coluna_uf` for
    passada.
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


# ---------------------------------------------------------------------------
# 4. ORQUESTRACAO POR FONTE — pontos de entrada usados pelo restante da app
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
# 5. UNIAO PNCP + TCE — empilha as duas fontes mantendo TODOS os campos
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

# ---------------------------------------------------------------------------
# 6. CLASSIFICACAO DE ORIGEM GEOGRAFICA — municipio do fornecedor/contratado
#    em relacao ao municipio do orgao comprador
# ---------------------------------------------------------------------------
 
import unicodedata
import unittest
from unittest.mock import MagicMock, patch
 
import requests
 
NIVEL_MESMO_MUNICIPIO = "Sediado no município comprador"
NIVEL_OUTRO_MUNICIPIO_CE = "Outro município do Ceará"
NIVEL_FORA_DO_ESTADO = "Fora do estado"
 
# Todas as 27 UFs do Brasil (26 estados + DF). Usada apenas para VALIDAR nos
# testes que a classificacao cobre qualquer estado fora do Ceara — a funcao
# `classificar_origem_geografica` em si NAO depende dessa lista: ela compara
# a UF do fornecedor com a UF do comprador de forma generica (uf_forn !=
# uf_comp), entao ja escala para qualquer UF sem precisar editar codigo.
UFS_BRASIL = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO",
    "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
    "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)
 
 
def normalizar_texto(texto: str | None) -> str:
    """
    Remove acentos e padroniza caixa/espacos de um texto livre (municipio/UF),
    para que a comparacao entre fontes diferentes (PNCP, TCE, IBGE) nao falhe
    por divergencia de formatacao (ex.: 'Fortaleza' vs 'FORTALEZA ').
    """
    texto = str(texto or "").strip().upper()
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
 
 
def classificar_origem_geografica(
    municipio_comprador: str,
    uf_fornecedor: str,
    municipio_fornecedor: str,
    uf_comprador: str = "CE",
) -> str:
    """
    Classifica a origem do fornecedor/contratado em relacao ao municipio do
    orgao comprador, em tres niveis:
 
        1) 'Sediado no município comprador' — mesma UF e mesmo municipio;
        2) 'Outro município do Ceará'       — mesma UF (CE), municipio diferente;
        3) 'Fora do estado'                 — UF diferente da do comprador.
 
    Recebe os valores ja resolvidos (ex.: 'municipio_nome'/'municipio_uf'
    trazidos por `enriquecer_com_municipio` para o lado comprador, e
    'fornecedor_*' — quando a base de fornecedores expuser municipio/UF —
    para o lado fornecedor). Nao faz nenhum join; apenas compara os textos
    ja normalizados.
    """
    uf_forn, uf_comp = normalizar_texto(uf_fornecedor), normalizar_texto(uf_comprador)
    if uf_forn != uf_comp:
        return NIVEL_FORA_DO_ESTADO
    mesmo_municipio = normalizar_texto(municipio_fornecedor) == normalizar_texto(municipio_comprador)
    return NIVEL_MESMO_MUNICIPIO if mesmo_municipio else NIVEL_OUTRO_MUNICIPIO_CE
 
 
def classificar_dataframe(
    df: pd.DataFrame,
    col_municipio_comprador: str = "municipio_comprador",
    col_uf_fornecedor: str = "uf_fornecedor",
    col_municipio_fornecedor: str = "municipio_fornecedor",
    uf_comprador: str = "CE",
    nova_coluna: str = "origem_geografica",
) -> pd.DataFrame:
    """
    Aplica `classificar_origem_geografica` a todas as linhas de `df`,
    adicionando a coluna `nova_coluna` com o resultado. Nao filtra nem
    remove linhas: quando faltar municipio/UF de algum dos lados, o
    resultado da linha reflete o texto vazio ja normalizado (na pratica,
    cai em 'Fora do estado' se a UF do fornecedor vier ausente).
    """
    df = df.copy()
    df[nova_coluna] = df.apply(
        lambda r: classificar_origem_geografica(
            r[col_municipio_comprador], r[col_uf_fornecedor], r[col_municipio_fornecedor], uf_comprador
        ),
        axis=1,
    )
    return df
 
 
def buscar_municipios_uf(uf: str = "CE") -> list[str]:
    """
    Busca, na API de Localidades do IBGE, os nomes de todos os municipios de
    uma UF (padrao: Ceara). Retorna a lista ordenada alfabeticamente, ja sem
    duplicatas.
 
    Endpoint: https://servicodados.ibge.gov.br/api/v1/localidades/estados/{UF}/municipios
 
    Pensado para escalar: recebe a UF como parametro (nao fixa "CE" no meio
    do codigo), entao serve tanto para validar/enriquecer a classificacao de
    origem geografica com a lista oficial de municipios do Ceara quanto para
    qualquer outro estado que o projeto passe a cobrir no futuro.
    """
    url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf.upper()}/municipios"
    resposta = requests.get(url, timeout=10)
    resposta.raise_for_status()
    dados = resposta.json()
    return sorted({item["nome"] for item in dados if "nome" in item})
 

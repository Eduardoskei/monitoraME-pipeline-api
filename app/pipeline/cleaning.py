"""
app/pipeline/cleaning.py

Modulo de limpeza (Data Cleaning) para os dados brutos coletados pelas
integracoes em app.pipeline.ingestion:

    - pncp.py          -> contratacoes, itens, resultados e contratos (PNCP)
    - tce.py           -> processos, contratos, contratados e itens (TCE-CE)
    - ibge.py          -> municipios (IBGE)
    - fornecedores.py  -> dados de CNPJ (OpenCNPJ)

Cada fonte tem seu proprio formato de JSON (aninhado, com nomes de campo em
camelCase, valores numericos como string, datas em formatos diferentes etc).
Este modulo aplica, de forma padronizada, as 5 regras de limpeza pedidas:

    1. Padronizacao de nomes  -> snake_case, sem acento, sem espaco extra
    2. Achatamento (flatten)  -> dicionarios aninhados viram colunas "pai_filho"
    3. Padronizacao de tipos  -> strings numericas -> int/float
                                  strings de data   -> ISO 8601 (YYYY-MM-DD[THH:MM:SS])
    4. Tratamento de nulos    -> remocao de linhas/colunas 100% vazias +
                                  preenchimento padrao para o restante
    5. Remocao de duplicatas  -> remove registros exatamente repetidos

Alem dessas 5, o motor generico tambem cobre validacoes de dominio que nao
sao pegas por conversao de tipo/nulo (secao 1.1/1.2):

    - Documentos (CNPJ/CPF)   -> normaliza para digitos e valida o digito
                                  verificador, sem descartar linha com DV
                                  invalido (so sinaliza em '<coluna>_valido')
    - Chaves de entidade      -> nome de orgao/empresa ganha uma coluna
                                  '<coluna>_chave' (sem acento/pontuacao,
                                  maiusculo) para nao fragmentar agregacoes
    - Fuso horario em datas   -> offset (‑03:00 etc.) e normalizado p/ UTC
                                  ('...Z') em vez de descartado ao serializar

O motor generico (secao 1) funciona para qualquer lista de dicts vinda de
qualquer API. As funcoes especificas por fonte (secao 2) so aplicam
conhecimento de dominio (quais colunas sao valor monetario, quais sao data,
quais sao a chave de identificacao do registro, porte da empresa). A secao 3
cobre validacoes que exigem cruzar 2 fontes ja limpas (ex.: municipio x UF
contra a base do IBGE).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import pandas as pd

from app.utils import remover_acentos, somente_digitos

# ---------------------------------------------------------------------------
# 1. MOTOR GENERICO — funciona para qualquer JSON bruto de API
# ---------------------------------------------------------------------------

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NAO_ALFANUM_RE = re.compile(r"[^a-z0-9]+")


def to_snake_case(nome: Any) -> str:
    """
    Converte qualquer nome de coluna/chave para snake_case:
    'valorTotalEstimado' -> 'valor_total_estimado'
    'CNPJ Orgao'         -> 'cnpj_orgao'
    ' Nome  Fantasia '   -> 'nome_fantasia'
    """
    nome = str(nome).strip()
    nome = _CAMEL_RE.sub("_", nome)          # separa camelCase
    nome = remover_acentos(nome).lower()
    nome = _NAO_ALFANUM_RE.sub("_", nome)    # espacos, pontos, / etc -> "_"
    return nome.strip("_")


def limpar_texto(valor: Any) -> Any:
    """Remove espacos extras (inicio/fim e duplicados no meio) de strings."""
    if not isinstance(valor, str):
        return valor
    valor = re.sub(r"\s+", " ", valor).strip()
    return valor if valor != "" else None


def flatten_registro(registro: dict[str, Any], parent_key: str = "", sep: str = "_") -> dict[str, Any]:
    """
    Achata um dict aninhado em um dict de 1 nivel.

    - dict aninhado (ex: {"orgaoEntidade": {"cnpj": "..."}})
        -> vira "orgao_entidade_cnpj"
    - lista de valores simples (ex: ["A", "B"])
        -> vira uma unica coluna com os valores unidos por "; "
    - lista de dicts (ex: itens de uma compra, contratos de um processo)
        -> NAO e forcada em colunas (isso geraria dezenas de colunas
           esparsas e um numero variavel de itens por linha, o que quebra
           o formato tabular). Em vez disso mantemos como lista de dicts
           ja com as chaves internas padronizadas, para ser explodida em
           uma tabela filha por `achatar_lista_para_tabelas` — pratica
           padrao de modelagem (1 tabela por entidade/relacao).
    """
    itens: dict[str, Any] = {}
    for chave, valor in registro.items():
        nova_chave = f"{parent_key}{sep}{chave}" if parent_key else chave

        if isinstance(valor, dict):
            itens.update(flatten_registro(valor, nova_chave, sep=sep))
        elif isinstance(valor, list):
            if len(valor) == 0:
                itens[nova_chave] = None
            elif all(not isinstance(v, (dict, list)) for v in valor):
                itens[nova_chave] = "; ".join(str(v) for v in valor if v not in (None, ""))
            else:
                # lista de dicts/listas -> mantida como está para virar tabela filha
                itens[nova_chave] = valor
        else:
            itens[nova_chave] = valor

    return itens


def achatar_registros(registros: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Achata uma lista de registros brutos (JSON) em um DataFrame plano."""
    achatados = [flatten_registro(r) for r in registros]
    return pd.DataFrame(achatados)


def achatar_lista_para_tabela(
    df: pd.DataFrame,
    coluna_lista: str,
    chave_pai: str | list[str],
) -> pd.DataFrame | None:
    """
    Extrai uma coluna que contem listas de dicts (ex: 'itens', 'contratos')
    e transforma em uma tabela filha independente, com a(s) chave(s) do
    registro pai preservada(s) para permitir o JOIN de volta.

    Retorna None se a coluna nao existir ou nao tiver listas de dicts.
    """
    if coluna_lista not in df.columns:
        return None

    chaves_pai = [chave_pai] if isinstance(chave_pai, str) else list(chave_pai)
    linhas = []
    for _, linha in df.iterrows():
        valor = linha[coluna_lista]
        if not isinstance(valor, list):
            continue
        for item in valor:
            if not isinstance(item, dict):
                continue
            achatado = flatten_registro(item)
            for chave in chaves_pai:
                achatado[chave] = linha[chave]
            linhas.append(achatado)

    if not linhas:
        return None

    filha = pd.DataFrame(linhas)
    filha.columns = [to_snake_case(c) for c in filha.columns]
    return filha


def padronizar_nomes_colunas(df: pd.DataFrame) -> pd.DataFrame:
    """Renomeia todas as colunas para snake_case, resolvendo colisoes de nome."""
    novos_nomes = [to_snake_case(c) for c in df.columns]

    # Evita colunas duplicadas apos a normalizacao (ex.: 'CNPJ' e 'cnpj'). O
    # sufixo gerado ('_1', '_2', ...) e checado contra TODOS os nomes finais
    # ja usados (nao so o nome-base) — senao um sufixo gerado pode colidir
    # com uma coluna original que ja tinha aquele nome (ex.: ['A','A','A_1']
    # viraria ['a','a_1','a_1'], um bug real encontrado em revisao de codigo).
    vistos: set[str] = set()
    finais = []
    for nome in novos_nomes:
        final = nome
        sufixo = 1
        while final in vistos:
            final = f"{nome}_{sufixo}"
            sufixo += 1
        vistos.add(final)
        finais.append(final)

    df = df.copy()
    df.columns = finais
    return df


def _e_coluna_texto(serie: pd.Series) -> bool:
    """
    True para colunas de texto genericas. Cobre tanto o dtype 'object'
    classico quanto o dtype 'str' nativo introduzido no pandas 3.x — sem
    isso, colunas de texto no pandas novo (dtype 'str') seriam ignoradas
    pelas rotinas de limpeza/conversao.
    """
    return pd.api.types.is_object_dtype(serie) or pd.api.types.is_string_dtype(serie)


def padronizar_textos(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica trim / colapso de espacos em todas as colunas de texto."""
    df = df.copy()
    for coluna in df.columns:
        if _e_coluna_texto(df[coluna]):
            df[coluna] = df[coluna].map(limpar_texto).astype(object)
    return df


# Colunas cujo nome indica um CODIGO/IDENTIFICADOR, nao uma quantidade.
# Mesmo parecendo numericas ("010", "0001"), NAO devem ser convertidas
# automaticamente: converter para int destruiria zeros a esquerda
# (codigo_municipio "010" -> 10) e o campo nunca e usado em conta/soma.
# 'numero' entrou apos confirmar ao vivo na API do TCE-CE que campos como
# 'numero_contrato'/'numero_processo_adm' vem como string puramente numerica
# (ex.: "2023004557") — sem essa protecao virariam float/Int64, perdendo o
# sentido de identificador (e correndo risco de ser somados por engano).
# 'ni' cobre 'ni_fornecedor' (PNCP: "Numero de Identificacao" do fornecedor,
# pode ser CNPJ ou CPF) — sem isso, um niFornecedor com zero a esquerda
# (ex.: "01234567000199") virava Int64 e perdia o digito, fazendo
# merge.enriquecer_com_fornecedor nao achar o fornecedor mesmo com o CNPJ
# correto na base (bug real, encontrado em revisao de codigo).
# Se o chamador realmente quiser converter uma dessas, basta passar o
# nome dela explicitamente em `colunas`.
_PADRAO_COLUNA_IDENTIFICADOR = re.compile(
    r"(^|_)(codigo|cod|cnpj|cpf|cep|cep8|inscricao|controle|protocolo|"
    r"telefone|celular|matricula|documento|numero|ni)($|_)"
)


def converter_numericos(df: pd.DataFrame, colunas: Iterable[str] | None = None) -> pd.DataFrame:
    """
    Converte colunas cujo conteudo e numerico (mas veio como string, com
    virgula decimal, R$, %, etc.) para float/int reais.

    Se `colunas` nao for informado, tenta detectar automaticamente: uma
    coluna e considerada numerica se, entre os valores efetivamente
    preenchidos, >= 90% conseguem virar numero — e o nome da coluna nao
    parece ser um identificador/codigo (ver `_PADRAO_COLUNA_IDENTIFICADOR`).
    """
    df = df.copy()
    candidatas = list(colunas) if colunas is not None else list(df.columns)

    def _para_numero(valor: Any) -> Any:
        if valor is None or (isinstance(valor, float) and pd.isna(valor)):
            return None
        if isinstance(valor, bool):
            # bool e subclasse de int em Python — sem este guard, uma coluna booleana
            # (ex.: 'opcao_pelo_simples') seria "convertida" para 1/0 (Int64) em vez
            # de continuar True/False.
            return None
        if isinstance(valor, (int, float)):
            return valor
        texto = str(valor).strip()
        if texto == "":
            return None
        texto = re.sub(r"[R$%\s]", "", texto)
        # formato brasileiro "1.234.567,89" -> "1234567.89"
        if re.match(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$", texto):
            texto = texto.replace(".", "").replace(",", ".")
        elif "," in texto and "." not in texto:
            texto = texto.replace(",", ".")
        try:
            return float(texto)
        except ValueError:
            return None

    for coluna in candidatas:
        if coluna not in df.columns:
            continue
        serie = df[coluna]
        if not _e_coluna_texto(serie):
            continue

        modo_explicito = colunas is not None
        if not modo_explicito and _PADRAO_COLUNA_IDENTIFICADOR.search(coluna):
            continue

        convertida = serie.map(_para_numero)
        mask_originais = serie.notna() & (serie.astype(str).str.strip() != "")

        if mask_originais.any():
            taxa_sucesso = convertida[mask_originais].notna().mean()
        else:
            taxa_sucesso = 0.0

        if modo_explicito or taxa_sucesso >= 0.9:
            # int quando todos os valores convertidos sao numeros inteiros exatos
            nao_nulos = convertida.dropna()
            if not nao_nulos.empty and nao_nulos.apply(lambda v: float(v).is_integer()).all():
                df[coluna] = convertida.astype("Int64")
            else:
                df[coluna] = convertida.astype("float64")

    return df


def converter_datas(df: pd.DataFrame, colunas: Iterable[str] | None = None) -> pd.DataFrame:
    """
    Converte colunas de data/hora para o padrao ISO 8601.

    Detecta automaticamente colunas cujo nome contenha 'data' ou 'dt_' (se
    `colunas` nao for informado) e tenta os formatos mais comuns usados
    pelas 3 APIs do projeto (PNCP: 'YYYYMMDD' ou ISO; TCE-CE: 'YYYY-MM-DD').
    Datas com hora sao serializadas como 'YYYY-MM-DDTHH:MM:SS'; datas sem
    hora, como 'YYYY-MM-DD'. Quando o valor original traz fuso horario (ex.:
    PNCP costuma retornar offset '-03:00'), o resultado e normalizado para
    UTC e serializado como 'YYYY-MM-DDTHH:MM:SSZ' — sem isso o offset seria
    descartado silenciosamente ao serializar, distorcendo prazos.
    """
    df = df.copy()
    if colunas is not None:
        candidatas = list(colunas)
    else:
        candidatas = [c for c in df.columns if re.search(r"(^|_)(data|dt)($|_)", c)]

    formatos = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d", "%d/%m/%Y")

    for coluna in candidatas:
        if coluna not in df.columns:
            continue

        def _para_iso(valor: Any) -> Any:
            if valor is None or (isinstance(valor, float) and pd.isna(valor)):
                return None
            if isinstance(valor, pd.Timestamp):
                if valor.tzinfo is not None:
                    return valor.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
                # mesma regra do parser flexivel abaixo: meia-noite exata vira
                # data-only, pra nao divergir do formato quando o mesmo valor
                # chega como string em vez de Timestamp (bug real encontrado
                # em revisao de codigo — isoformat() sempre incluia o horario)
                return (
                    valor.strftime("%Y-%m-%d")
                    if valor.time() == valor.time().min
                    else valor.strftime("%Y-%m-%dT%H:%M:%S")
                )
            texto = str(valor).strip()
            if texto == "":
                return None
            for formato in formatos:
                try:
                    dt = pd.to_datetime(texto, format=formato)
                    tem_hora = "%H" in formato
                    return dt.strftime("%Y-%m-%dT%H:%M:%S") if tem_hora else dt.strftime("%Y-%m-%d")
                except ValueError:
                    continue
            # formatos fixos nao bateram: pode ter fuso horario (ex.: '-03:00', 'Z') ->
            # parser flexivel, que entende offset
            try:
                dt = pd.to_datetime(texto, errors="raise")
            except (ValueError, TypeError):
                return None
            if dt.tzinfo is not None:
                return dt.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
            return dt.strftime("%Y-%m-%d") if dt.time() == dt.time().min else dt.strftime("%Y-%m-%dT%H:%M:%S")

        df[coluna] = df[coluna].map(_para_iso)

    return df


def tratar_nulos(
    df: pd.DataFrame,
    *,
    colunas_obrigatorias: Iterable[str] | None = None,
    limite_coluna_vazia: float = 0.98,
    preencher_texto_com: str | None = "nao_informado",
) -> pd.DataFrame:
    """
    Estrategia de nulos (nao existe "uma" resposta certa — a escolha aqui
    e a que preserva mais informacao sem inventar dado que nao existe):

      1. Remove colunas quase 100% vazias (>= `limite_coluna_vazia`), pois
         nao carregam informacao aproveitavel.
      2. Remove linhas totalmente vazias (registro "fantasma").
      3. Remove linhas sem as `colunas_obrigatorias` (chaves de
         identificacao do registro, ex.: cnpj/ano/sequencial) — sem elas o
         registro nao pode ser relacionado a nada.
      4. Para o restante:
           - colunas numericas: mantem NaN (preencher valor monetario/
             quantidade ausente com 0 mudaria o significado do dado —
             fica sinalizavel via `df[col].isna()`).
           - colunas de texto: preenche com um marcador padrao
             (`preencher_texto_com`), configuravel, para nao quebrar
             agrupamentos/filtros a jusante.
    """
    df = df.copy()

    # 1. colunas quase todas vazias
    limite_nao_nulos = max(1, int(len(df) * (1 - limite_coluna_vazia)))
    df = df.dropna(axis=1, thresh=limite_nao_nulos)

    # 2. linhas totalmente vazias
    df = df.dropna(axis=0, how="all")

    # 3. linhas sem chave de identificacao
    if colunas_obrigatorias:
        colunas_existentes = [c for c in colunas_obrigatorias if c in df.columns]
        if colunas_existentes:
            df = df.dropna(subset=colunas_existentes, how="any")

    # 4. preenchimento do restante
    if preencher_texto_com is not None:
        colunas_texto = [c for c in df.columns if _e_coluna_texto(df[c])]
        for coluna in colunas_texto:
            df[coluna] = df[coluna].fillna(preencher_texto_com)

    return df.reset_index(drop=True)


def remover_duplicatas(df: pd.DataFrame, subset: Iterable[str] | None = None) -> pd.DataFrame:
    """Remove registros exatamente repetidos (ou repetidos pela chave em `subset`)."""
    antes = len(df)
    df = df.drop_duplicates(subset=list(subset) if subset else None, keep="first").reset_index(drop=True)
    removidos = antes - len(df)
    if removidos:
        print(f"[cleaning] {removidos} registro(s) duplicado(s) removido(s).")
    return df


# ---------------------------------------------------------------------------
# 1.1 DOCUMENTOS (CNPJ/CPF) — normalizacao + validacao de digito verificador
# ---------------------------------------------------------------------------

_PADRAO_COLUNA_CNPJ = re.compile(r"(^|_)cnpj($|_)")
_PADRAO_COLUNA_CPF = re.compile(r"(^|_)cpf($|_)")


def _digito_verificador(base: str, pesos: list[int]) -> int:
    soma = sum(int(digito) * peso for digito, peso in zip(base, pesos))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def normalizar_cnpj(valor: Any) -> str | None:
    """Reduz a apenas digitos; retorna None se nao tiver os 14 digitos esperados
    (nesse caso o valor e inutilizavel como chave e deve ser tratado como ausente)."""
    digitos = somente_digitos(valor)
    return digitos if len(digitos) == 14 else None


def normalizar_cpf(valor: Any) -> str | None:
    """Reduz a apenas digitos; retorna None se nao tiver os 11 digitos esperados."""
    digitos = somente_digitos(valor)
    return digitos if len(digitos) == 11 else None


def validar_cnpj(cnpj: str | None) -> bool:
    """Confere os 2 digitos verificadores. Um CNPJ com 14 digitos mas DV invalido
    NAO e descartado pelo pipeline — fica sinalizado (coluna '<nome>_valido') para
    auditoria, em vez de sumir silenciosamente do dado."""
    if not isinstance(cnpj, str) or len(cnpj) != 14 or len(set(cnpj)) == 1:
        return False
    dv1 = _digito_verificador(cnpj[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    dv2 = _digito_verificador(cnpj[:12] + str(dv1), [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return cnpj[-2:] == f"{dv1}{dv2}"


def validar_cpf(cpf: str | None) -> bool:
    """Confere os 2 digitos verificadores do CPF (mesma logica de validar_cnpj)."""
    if not isinstance(cpf, str) or len(cpf) != 11 or len(set(cpf)) == 1:
        return False
    dv1 = _digito_verificador(cpf[:9], [10, 9, 8, 7, 6, 5, 4, 3, 2])
    dv2 = _digito_verificador(cpf[:9] + str(dv1), [11, 10, 9, 8, 7, 6, 5, 4, 3, 2])
    return cpf[-2:] == f"{dv1}{dv2}"


def padronizar_documentos(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detecta automaticamente qualquer coluna 'cnpj'/'cpf' (ex.: 'cnpj', 'orgao_entidade_cnpj',
    'cnpj_contratado') e:
      1. normaliza para digitos puros (mesmo padrao em todas as fontes — antes, PNCP/TCE/
         Fornecedores podiam representar o mesmo CNPJ em formatos diferentes, quebrando
         o JOIN entre as bases);
      2. adiciona uma coluna booleana '<coluna>_valido' com o resultado da validacao do
         digito verificador, sem remover a linha (o valor pode ainda ser util para
         auditoria mesmo com o DV invalido).
    """
    df = df.copy()
    for coluna in list(df.columns):
        if coluna.endswith("_valido"):
            continue
        if _PADRAO_COLUNA_CNPJ.search(coluna):
            normalizados = df[coluna].map(normalizar_cnpj).astype(object)
            normalizados = normalizados.where(pd.notna(normalizados), None)
            df[f"{coluna}_valido"] = normalizados.map(lambda v: validar_cnpj(v) if v else False)
            df[coluna] = normalizados
        elif _PADRAO_COLUNA_CPF.search(coluna):
            normalizados = df[coluna].map(normalizar_cpf).astype(object)
            normalizados = normalizados.where(pd.notna(normalizados), None)
            df[f"{coluna}_valido"] = normalizados.map(lambda v: validar_cpf(v) if v else False)
            df[coluna] = normalizados
    return df


# ---------------------------------------------------------------------------
# 1.2 CHAVES DE ENTIDADE — nome de orgao/secretaria/fornecedor normalizado p/ agrupamento
# ---------------------------------------------------------------------------

_PADRAO_COLUNA_ENTIDADE = re.compile(
    r"(^|_)(razao_social|nome_fantasia|nome_orgao|nome_contratado|nome_unidade|nome_socio)($|_)"
)
_PONTUACAO_ENTIDADE_RE = re.compile(r"[.,;:/\\-]")


def normalizar_chave_entidade(texto: Any) -> str | None:
    """
    Reduz um nome de orgao/empresa a uma forma comparavel: sem acento, maiusculo,
    sem pontuacao, espacos colapsados. NAO substitui o texto original (que continua
    legivel na coluna de origem) — serve apenas como chave de agrupamento/join, para
    que 'Prefeitura Municipal de Amontada' e 'PREFEITURA MUN. DE AMONTADA' caiam na
    mesma chave em vez de fragmentar o agregado por orgao.
    """
    if not isinstance(texto, str) or texto.strip() == "":
        return None
    chave = remover_acentos(texto).upper()
    chave = _PONTUACAO_ENTIDADE_RE.sub(" ", chave)
    chave = re.sub(r"\s+", " ", chave).strip()
    return chave or None


def padronizar_chaves_entidades(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona '<coluna>_chave' para toda coluna de nome de orgao/empresa detectada (ver
    `normalizar_chave_entidade`)."""
    df = df.copy()
    for coluna in list(df.columns):
        if _PADRAO_COLUNA_ENTIDADE.search(coluna) and _e_coluna_texto(df[coluna]):
            df[f"{coluna}_chave"] = df[coluna].map(normalizar_chave_entidade)
    return df


def limpar_generico(
    registros: Iterable[dict[str, Any]],
    *,
    colunas_obrigatorias: Iterable[str] | None = None,
    colunas_data: Iterable[str] | None = None,
    colunas_numericas: Iterable[str] | None = None,
    chave_duplicata: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Pipeline completo: flatten -> nomes -> tipos -> nulos -> duplicatas."""
    df = achatar_registros(registros)
    if df.empty:
        return df

    df = padronizar_nomes_colunas(df)
    df = padronizar_textos(df)
    df = converter_numericos(df, colunas_numericas)
    df = converter_datas(df, colunas_data)
    df = padronizar_documentos(df)
    df = padronizar_chaves_entidades(df)
    df = tratar_nulos(df, colunas_obrigatorias=colunas_obrigatorias)
    df = remover_duplicatas(df, subset=chave_duplicata)
    return df


# ---------------------------------------------------------------------------
# 2. PIPELINES ESPECIFICOS POR FONTE
#    (aplicam conhecimento de dominio sobre cada API do projeto)
# ---------------------------------------------------------------------------

def limpar_pncp_contratacoes(registros: list[dict[str, Any]]) -> dict[str, pd.DataFrame]:
    """
    Limpa contratacoes do PNCP (retorno de `pncp.buscar_contratacoes_publicadas`
    ou o campo 'publicacao' de `pncp.coletar_compras_publicadas`).

    Retorna um dict de tabelas:
      - 'contratacoes': 1 linha por compra/contratacao
      - 'itens'/'contratos': tabelas filhas, se presentes nos registros
        (quando `incluir_detalhes=True` foi usado na coleta)
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


def limpar_tce(registros: list[dict[str, Any]], chave_duplicata: list[str] | None = None) -> pd.DataFrame:
    """Limpa qualquer um dos 4 endpoints do TCE-CE (contratacoes/contratos/contratados/itens)."""
    colunas_data = None  # detectadas automaticamente pelo prefixo/sufixo 'data'
    return limpar_generico(
        registros,
        colunas_data=colunas_data,
        chave_duplicata=chave_duplicata,
    )


def limpar_ibge_municipios(registros: list[dict[str, Any]]) -> pd.DataFrame:
    """Limpa a lista de municipios do IBGE (estrutura fortemente aninhada: microrregiao > mesorregiao > UF > regiao)."""
    df = limpar_generico(
        registros,
        colunas_obrigatorias=["id"],
        chave_duplicata=["id"],
    )
    if "id" in df.columns:
        df["id"] = df["id"].astype("Int64")
    return df


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
    # codigos numericos usados por algumas fontes (Receita Federal/Simples Nacional)
    "1": "MEI",
    "2": "ME",
    "3": "EPP",
    "5": "DEMAIS",
}


def normalizar_porte_empresarial(valor: Any) -> str | None:
    """
    Mapeia as variacoes de 'porte' usadas pelas fontes cadastrais de CNPJ
    para um vocabulario fixo (MEI/ME/EPP/DEMAIS). A analise do projeto usa ME
    estrita, mas manter a categoria original padronizada permite distinguir EPP,
    MEI e demais sem misturar tudo em um booleano.
    Retorna None (nao mapeado) em vez de chutar uma categoria, para nao mascarar
    um valor novo/desconhecido vindo da API.
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


def limpar_fornecedores(registros: list[dict[str, Any]]) -> pd.DataFrame:
    """
    Limpa o retorno de `fornecedores.coletar_fornecedor`, que consulta a
    OpenCNPJ e preserva o payload bruto com prefixo 'opencnpj_' apos o flatten.

    Alem do pipeline generico (que ja normaliza/valida o cnpj via
    `padronizar_documentos`), adiciona:
      - 'porte_padronizado'/'elegivel_me' a partir do campo 'porte' (ME estrita);
      - 'optante_simples_nacional'/'optante_mei' (+ datas de opcao/exclusao),
        quando a OpenCNPJ retornar esses campos.

    Sao dois criterios distintos de "ser ME" — porte cadastral e regime tributario
    optado nao sao sinonimos (uma empresa pode ser ME por faturamento e nao ter
    optado pelo Simples) — por isso ficam como colunas separadas em vez de um
    substituir o outro; quem consumir decide qual usar para cada analise.
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
            "opencnpj_porte",
            "opencnpj_descricao_porte",
            "opencnpj_porte_descricao",
            "opencnpj_empresa_porte",
            "opencnpj_empresa_porte_descricao",
            "opencnpj_estabelecimento_porte",
            "opencnpj_estabelecimento_porte_descricao",
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
                "opencnpj_opcao_pelo_simples",
                "opencnpj_simples_opcao_pelo_simples",
                "opencnpj_empresa_opcao_pelo_simples",
                "opencnpj_estabelecimento_opcao_pelo_simples",
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
                "opencnpj_data_opcao_pelo_simples",
                "opencnpj_simples_data_opcao_pelo_simples",
                "opencnpj_empresa_data_opcao_pelo_simples",
                "opencnpj_estabelecimento_data_opcao_pelo_simples",
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
                "opencnpj_data_exclusao_do_simples",
                "opencnpj_simples_data_exclusao_do_simples",
                "opencnpj_empresa_data_exclusao_do_simples",
                "opencnpj_estabelecimento_data_exclusao_do_simples",
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
                "opencnpj_opcao_pelo_mei",
                "opencnpj_simples_opcao_pelo_mei",
                "opencnpj_empresa_opcao_pelo_mei",
                "opencnpj_estabelecimento_opcao_pelo_mei",
            )
            if c in df.columns
        ),
        None,
    )
    if coluna_mei:
        df["optante_mei"] = df[coluna_mei].map(normalizar_booleano).astype("boolean")

    return df


# ---------------------------------------------------------------------------
# 2.1 NORMALIZACAO DE PORTE — PNCP
# ---------------------------------------------------------------------------

# O PNCP usa codigos proprios para representar o porte do fornecedor.
_MAPA_PORTE_PNCP = {
    "1": "ME",
    "2": "EPP",
    "3": "DEMAIS",
    "4": "NAO_SE_APLICA",
    "5": "NAO_INFORMADO",
}

_MAPA_DESCRICAO_PORTE_PNCP = {
    "MEI": "MEI",
    "MICROEMPREENDEDOR_INDIVIDUAL": "MEI",
}

_PORTES_VALIDOS_PNCP = (
    set(_MAPA_PORTE_PNCP.values())
    | set(_MAPA_DESCRICAO_PORTE_PNCP.values())
)


def normalizar_porte_pncp(valor: Any) -> str | None:
    """Normaliza o codigo ou a descricao de porte informado pelo PNCP."""
    if valor is None:
        return None

    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        return None

    texto = str(valor).strip()
    if not texto:
        return None

    porte = remover_acentos(texto).upper()
    porte = re.sub(r"[\s-]+", "_", porte)

    if porte in _PORTES_VALIDOS_PNCP:
        return porte
    if porte in _MAPA_DESCRICAO_PORTE_PNCP:
        return _MAPA_DESCRICAO_PORTE_PNCP[porte]

    try:
        codigo = Decimal(texto)
    except InvalidOperation:
        return None

    if not codigo.is_finite() or codigo != codigo.to_integral_value():
        return None

    return _MAPA_PORTE_PNCP.get(str(int(codigo)))


def padronizar_porte_pncp(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona o porte padronizado sem alterar o DataFrame original."""
    coluna_destino = "porte_fornecedor_padronizado"
    resultado = df.copy()

    colunas_origem = [
        coluna
        for coluna in ("porte_fornecedor_nome", "porte_fornecedor_id")
        if coluna in resultado.columns
    ]
    if not colunas_origem:
        return resultado

    porte = resultado[colunas_origem[0]].map(normalizar_porte_pncp)
    for coluna in colunas_origem[1:]:
        porte = porte.combine_first(
            resultado[coluna].map(normalizar_porte_pncp)
        )

    resultado[coluna_destino] = porte.astype("string")

    return resultado


def limpar_pncp_resultados(registros: list[dict[str, Any]]) -> pd.DataFrame:
    """Limpa os resultados dos itens do PNCP e padroniza o porte."""
    df = limpar_generico(registros)
    return padronizar_porte_pncp(df)


# ---------------------------------------------------------------------------
# 3. VALIDACAO CRUZADA ENTRE FONTES
# ---------------------------------------------------------------------------

def validar_municipio_uf(
    df: pd.DataFrame,
    municipios_ibge: pd.DataFrame,
    *,
    coluna_codigo_municipio: str,
    coluna_uf: str,
) -> pd.DataFrame:
    """
    Cruza o codigo de municipio (IBGE) informado por uma fonte (ex.: PNCP:
    'unidade_orgao_codigo_ibge'; TCE: 'codigo_municipio') contra a base oficial
    ja limpa por `limpar_ibge_municipios`, e sinaliza em
    '<coluna_codigo_municipio>_uf_confere' se a UF informada bate com a UF
    oficial do municipio.

    Nao remove nem corrige nada — hoje nenhuma etapa detecta um municipio
    atribuido a UF errada (ex.: erro de digitacao no codigo IBGE de uma
    contratacao); esta funcao so torna essa inconsistencia visivel/filtravel.
    O resultado e None (nao verificado) quando o codigo informado nao existe
    na base do IBGE ou esta ausente, para nao confundir "nao verificavel" com
    "incorreto".

    Precisa ser chamada explicitamente pelo orquestrador (ex.: em merge.py)
    depois de `limpar_ibge_municipios` e da limpeza da fonte a validar, pois
    depende das duas tabelas ja limpas.
    """
    df = df.copy()
    coluna_resultado = f"{coluna_codigo_municipio}_uf_confere"

    if coluna_codigo_municipio not in df.columns or coluna_uf not in df.columns:
        return df
    if "id" not in municipios_ibge.columns or "microrregiao_mesorregiao_uf_sigla" not in municipios_ibge.columns:
        return df

    # Vetorizado (merge/map) em vez de df.apply(..., axis=1): um apply linha a
    # linha vira gargalo real quando esta funcao roda sobre uma tabela de
    # contratacoes de escala estadual/nacional (encontrado em revisao de codigo).
    referencia = municipios_ibge.dropna(subset=["id"]).set_index("id")["microrregiao_mesorregiao_uf_sigla"]

    codigo_numerico = pd.to_numeric(df[coluna_codigo_municipio], errors="coerce").astype("Int64")
    uf_oficial = codigo_numerico.map(referencia)

    nao_verificavel = uf_oficial.isna() | df[coluna_uf].isna()
    confere = uf_oficial.astype(str).str.strip().str.upper() == df[coluna_uf].astype(str).str.strip().str.upper()

    df[coluna_resultado] = confere.where(~nao_verificavel, None)
    return df

from datetime import datetime
import re
from typing import Any, Iterable
import unicodedata

import pandas as pd


VALORES_VAZIOS = (None, "")
MARCADORES_BANCO_INDISPONIVEL = ("psycopg2-binary",)


def somente_digitos(valor: Any) -> str:
    """Retorna apenas os digitos de um valor qualquer."""
    if valor is None:
        return ""
    return "".join(caractere for caractere in str(valor) if caractere.isdigit())


def remover_acentos(texto: str) -> str:
    """Remove acentos/diacriticos mantendo o restante do texto intacto."""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalizar_texto(valor: Any) -> str:
    """Texto maiusculo, sem acentos, hifens e espacos duplicados."""
    if valor is None:
        return ""
    texto = remover_acentos(str(valor).strip()).upper()
    return " ".join(texto.replace("-", " ").split())


def valor_preenchido(valor: Any, marcadores_vazios: Iterable[Any] = VALORES_VAZIOS) -> bool:
    """True para valores diferentes dos vazios usados pelas APIs."""
    marcadores = tuple(marcadores_vazios)
    if valor is None:
        return False
    try:
        nulo = pd.isna(valor)
        if not isinstance(nulo, (list, tuple)) and not getattr(nulo, "shape", None) and bool(nulo):
            return False
    except (TypeError, ValueError):
        pass

    if isinstance(valor, str):
        texto = valor.strip()
        if texto == "":
            return False
        marcadores_texto = {
            marcador.strip()
            for marcador in marcadores
            if isinstance(marcador, str) and marcador.strip() != ""
        }
        return texto not in marcadores_texto

    try:
        return valor not in marcadores
    except (TypeError, ValueError):
        return True


def primeira_coluna_preenchida(
    df: pd.DataFrame,
    colunas: Iterable[str],
    *,
    marcadores_vazios: Iterable[Any] = VALORES_VAZIOS,
) -> pd.Series:
    """
    Retorna, linha a linha, o primeiro valor preenchido entre colunas candidatas.

    Colunas ausentes sao ignoradas para permitir cleaners resilientes a
    pequenas variacoes de schema das fontes externas.
    """
    resultado = pd.Series([pd.NA] * len(df), index=df.index, dtype=object)
    preenchido = pd.Series([False] * len(df), index=df.index)

    for coluna in colunas:
        if coluna not in df.columns:
            continue
        serie = df[coluna].astype(object)
        usar = ~preenchido & serie.map(lambda valor: valor_preenchido(valor, marcadores_vazios))
        resultado.loc[usar] = serie.loc[usar]
        preenchido.loc[usar] = True

    return resultado


def get_nested(registro: dict[str, Any], caminho: tuple[str, ...]) -> Any:
    """Busca um valor em um dict aninhado; retorna None se o caminho quebrar."""
    atual: Any = registro
    for chave in caminho:
        if not isinstance(atual, dict):
            return None
        atual = atual.get(chave)
    return atual


def primeiro_valor(registro: dict[str, Any], caminhos: Iterable[tuple[str, ...]]) -> Any:
    """Retorna o primeiro valor nao vazio entre varios caminhos aninhados."""
    for caminho in caminhos:
        valor = get_nested(registro, caminho)
        if valor_preenchido(valor):
            return valor
    return None


def filtrar_params_vazios(params: dict[str, Any]) -> dict[str, Any]:
    """Remove parametros None ou string vazia antes de chamadas HTTP."""
    return {chave: valor for chave, valor in params.items() if valor_preenchido(valor)}


def banco_indisponivel(error: RuntimeError) -> bool:
    """Identifica erros esperados quando o cache Postgres nao esta pronto."""
    mensagem = str(error)
    return any(marcador in mensagem for marcador in MARCADORES_BANCO_INDISPONIVEL)


def normalizar_data(
    data: str,
    formatos_entrada: Iterable[str],
    formato_saida: str,
    formatos_aceitos: str,
) -> str:
    """Normaliza uma data string usando formatos permitidos explicitamente."""
    if not isinstance(data, str):
        raise TypeError(f"Data deve ser str, nao {type(data).__name__}.")

    data = data.strip()
    for formato in formatos_entrada:
        try:
            return datetime.strptime(data, formato).strftime(formato_saida)
        except ValueError:
            pass

    raise ValueError(f"Data invalida: {data!r}. Use {formatos_aceitos}.")


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
    nome = _CAMEL_RE.sub("_", nome)
    nome = remover_acentos(nome).lower()
    nome = _NAO_ALFANUM_RE.sub("_", nome)
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

    - dict aninhado vira uma coluna "pai_filho";
    - lista de valores simples vira uma unica string separada por "; ";
    - lista de dicts/listas e mantida para virar tabela filha depois.
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
    Extrai uma coluna que contem listas de dicts e transforma em tabela filha,
    preservando a(s) chave(s) do registro pai para permitir JOIN de volta.
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
    """True para colunas de texto genericas, incluindo dtypes string novos."""
    return pd.api.types.is_object_dtype(serie) or pd.api.types.is_string_dtype(serie)


def padronizar_textos(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica trim / colapso de espacos em todas as colunas de texto."""
    df = df.copy()
    for coluna in df.columns:
        if _e_coluna_texto(df[coluna]):
            df[coluna] = df[coluna].map(limpar_texto).astype(object)
    return df


_PADRAO_COLUNA_IDENTIFICADOR = re.compile(
    r"(^|_)(codigo|cod|cnpj|cpf|cep|cep8|inscricao|controle|protocolo|"
    r"telefone|celular|matricula|documento|numero|ni|cnae)($|_)"
)


def converter_numericos(df: pd.DataFrame, colunas: Iterable[str] | None = None) -> pd.DataFrame:
    """
    Converte colunas cujo conteudo e numerico para float/int reais.

    Sem `colunas`, detecta automaticamente quando >= 90% dos valores preenchidos
    conseguem virar numero e o nome da coluna nao parece identificador/codigo.
    """
    df = df.copy()
    candidatas = list(colunas) if colunas is not None else list(df.columns)

    def _para_numero(valor: Any) -> Any:
        if valor is None or (isinstance(valor, float) and pd.isna(valor)):
            return None
        if isinstance(valor, bool):
            return None
        if isinstance(valor, (int, float)):
            return valor
        texto = str(valor).strip()
        if texto == "":
            return None
        texto = re.sub(r"[R$%\s]", "", texto)
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
        taxa_sucesso = convertida[mask_originais].notna().mean() if mask_originais.any() else 0.0

        if modo_explicito or taxa_sucesso >= 0.9:
            nao_nulos = convertida.dropna()
            if not nao_nulos.empty and nao_nulos.apply(lambda v: float(v).is_integer()).all():
                df[coluna] = convertida.astype("Int64")
            else:
                df[coluna] = convertida.astype("float64")

    return df


def converter_datas(df: pd.DataFrame, colunas: Iterable[str] | None = None) -> pd.DataFrame:
    """
    Converte colunas de data/hora para ISO 8601.

    Datas com fuso horario sao normalizadas para UTC e serializadas com sufixo Z.
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
    """Remove vazios sem perder informacao util e preenche nulos textuais."""
    df = df.copy()

    limite_nao_nulos = max(1, int(len(df) * (1 - limite_coluna_vazia)))
    df = df.dropna(axis=1, thresh=limite_nao_nulos)
    df = df.dropna(axis=0, how="all")

    if colunas_obrigatorias:
        colunas_existentes = [c for c in colunas_obrigatorias if c in df.columns]
        if colunas_existentes:
            df = df.dropna(subset=colunas_existentes, how="any")

    if preencher_texto_com is not None:
        colunas_texto = [c for c in df.columns if _e_coluna_texto(df[c])]
        for coluna in colunas_texto:
            df[coluna] = df[coluna].fillna(preencher_texto_com)

    return df.reset_index(drop=True)


def remover_duplicatas(df: pd.DataFrame, subset: Iterable[str] | None = None) -> pd.DataFrame:
    """Remove registros exatamente repetidos ou repetidos pela chave em `subset`."""
    antes = len(df)
    df = df.drop_duplicates(subset=list(subset) if subset else None, keep="first").reset_index(drop=True)
    removidos = antes - len(df)
    if removidos:
        print(f"[cleaners] {removidos} registro(s) duplicado(s) removido(s).")
    return df


_PADRAO_COLUNA_CNPJ = re.compile(r"(^|_)cnpj($|_)")
_PADRAO_COLUNA_CPF = re.compile(r"(^|_)cpf($|_)")


def _digito_verificador(base: str, pesos: list[int]) -> int:
    soma = sum(int(digito) * peso for digito, peso in zip(base, pesos))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def normalizar_cnpj(valor: Any) -> str | None:
    """Reduz a apenas digitos; retorna None se nao tiver 14 digitos."""
    digitos = somente_digitos(valor)
    return digitos if len(digitos) == 14 else None


def normalizar_cpf(valor: Any) -> str | None:
    """Reduz a apenas digitos; retorna None se nao tiver 11 digitos."""
    digitos = somente_digitos(valor)
    return digitos if len(digitos) == 11 else None


def validar_cnpj(cnpj: str | None) -> bool:
    """Confere os 2 digitos verificadores do CNPJ."""
    if not isinstance(cnpj, str) or len(cnpj) != 14 or len(set(cnpj)) == 1:
        return False
    dv1 = _digito_verificador(cnpj[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    dv2 = _digito_verificador(cnpj[:12] + str(dv1), [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return cnpj[-2:] == f"{dv1}{dv2}"


def validar_cpf(cpf: str | None) -> bool:
    """Confere os 2 digitos verificadores do CPF."""
    if not isinstance(cpf, str) or len(cpf) != 11 or len(set(cpf)) == 1:
        return False
    dv1 = _digito_verificador(cpf[:9], [10, 9, 8, 7, 6, 5, 4, 3, 2])
    dv2 = _digito_verificador(cpf[:9] + str(dv1), [11, 10, 9, 8, 7, 6, 5, 4, 3, 2])
    return cpf[-2:] == f"{dv1}{dv2}"


def padronizar_documentos(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza colunas CNPJ/CPF e adiciona colunas booleanas de validade."""
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


_PADRAO_COLUNA_ENTIDADE = re.compile(
    r"(^|_)(razao_social|nome_fantasia|nome_orgao|nome_contratado|nome_unidade|nome_socio)($|_)"
)
_PONTUACAO_ENTIDADE_RE = re.compile(r"[.,;:/\\-]")


def normalizar_chave_entidade(texto: Any) -> str | None:
    """
    Reduz um nome de orgao/empresa a uma forma comparavel para agrupamento/join.
    """
    if not isinstance(texto, str) or texto.strip() == "":
        return None
    chave = remover_acentos(texto).upper()
    chave = _PONTUACAO_ENTIDADE_RE.sub(" ", chave)
    chave = re.sub(r"\s+", " ", chave).strip()
    return chave or None


def padronizar_chaves_entidades(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona '<coluna>_chave' para colunas de nome de orgao/empresa."""
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

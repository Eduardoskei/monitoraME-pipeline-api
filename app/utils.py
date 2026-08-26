from datetime import datetime
from typing import Any, Iterable
import unicodedata


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


def valor_preenchido(valor: Any) -> bool:
    """True para valores diferentes dos vazios usados pelas APIs."""
    if valor is None:
        return False
    if isinstance(valor, str):
        return valor != ""

    try:
        return bool(valor != "")
    except (TypeError, ValueError):
        return True


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

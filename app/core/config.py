import os
from dotenv import load_dotenv

load_dotenv()

class ConfigError(RuntimeError):
    pass


def _env(key: str, *, strip_slash: bool = False) -> str:
    value = os.getenv(key)
    if value is None or not value.strip():
        raise ConfigError(f"Variavel de ambiente obrigatoria ausente: {key}")

    value = value.strip()
    return value.rstrip("/") if strip_slash else value


def _int_env(key: str) -> int:
    value = _env(key)
    try:
        return int(value)
    except ValueError as error:
        raise ConfigError(f"Variavel de ambiente {key} deve ser um inteiro.") from error


def _optional_int_env(key: str, default: int, *, minimo: int = 1) -> int:
    value = os.getenv(key)
    if value is None or not value.strip():
        return default

    try:
        numero = int(value.strip())
    except ValueError as error:
        raise ConfigError(f"Variavel de ambiente {key} deve ser um inteiro.") from error

    if numero < minimo:
        raise ConfigError(f"Variavel de ambiente {key} deve ser maior ou igual a {minimo}.")
    return numero


def _int_tuple_env(key: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = os.getenv(key)
    if value is None or not value.strip():
        return default

    valores: list[int] = []
    for parte in value.split(","):
        texto = parte.strip()
        if not texto:
            continue
        try:
            numero = int(texto)
        except ValueError as error:
            raise ConfigError(f"Variavel de ambiente {key} deve conter apenas inteiros.") from error
        if numero < 1:
            raise ConfigError(f"Variavel de ambiente {key} deve conter apenas inteiros positivos.")
        valores.append(numero)

    if not valores:
        raise ConfigError(f"Variavel de ambiente {key} deve conter ao menos um valor.")
    return tuple(dict.fromkeys(valores))


def _str_tuple_env(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(key)
    if value is None or not value.strip():
        return default

    valores = tuple(dict.fromkeys(parte.strip().upper() for parte in value.split(",") if parte.strip()))
    if not valores:
        raise ConfigError(f"Variavel de ambiente {key} deve conter ao menos um valor.")
    return valores


DATABASE_URL = _env("DATABASE_URL")
LOG_DATABASE_URL = _env("LOG_DATABASE_URL")
TCE_CE_BASE_URL = _env("TCE_CE_BASE_URL", strip_slash=True)
IBGE_LOCALIDADES_BASE_URL = _env("IBGE_LOCALIDADES_BASE_URL", strip_slash=True)
PNCP_CONSULTA_BASE_URL = _env("PNCP_CONSULTA_BASE_URL", strip_slash=True)
PNCP_GESTAO_BASE_URL = _env("PNCP_GESTAO_BASE_URL", strip_slash=True)
OPENCNPJ_BASE_URL = _env("OPENCNPJ_BASE_URL", strip_slash=True)

UF_PADRAO = _env("UF_PADRAO")
CODIGO_IBGE_PADRAO = _env("CODIGO_IBGE_PADRAO")
CODIGO_MUNICIPIO_TCE_PADRAO = _env("CODIGO_MUNICIPIO_TCE_PADRAO")
MODALIDADE_ID_PADRAO = _int_env("MODALIDADE_ID_PADRAO")
PNCP_MODALIDADES_INCREMENTAIS = _int_tuple_env("PNCP_MODALIDADES_INCREMENTAIS", (MODALIDADE_ID_PADRAO,))
PNCP_UFS_INCREMENTAIS = _str_tuple_env("PNCP_UFS_INCREMENTAIS", (UF_PADRAO,))
PNCP_JANELA_INICIAL_HORAS = _optional_int_env("PNCP_JANELA_INICIAL_HORAS", 6)

# Escopo de despesas acompanhado pelo monitoraME. Esta tupla e a unica fonte
# de verdade do filtro aplicado aos registros recebidos das fontes externas.
NATUREZAS_DESPESA_CONSIDERADAS = (
    "Outros serviços de terceiros-pessoa jurídica",
    "Material de consumo",
    "Obras e instalações",
    "Equipamentos e material permanente",
    "Material, bem ou serviço para distribuição gratuita",
    "Serviços de tecnologia da informação e comunicação pessoa jurídica",
    "Serviços de consultoria",
)

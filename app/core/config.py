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

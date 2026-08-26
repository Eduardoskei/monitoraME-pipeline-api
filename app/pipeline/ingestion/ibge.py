from typing import Any
import time
import requests
from app.core.config import IBGE_LOCALIDADES_BASE_URL
from app.core import database
from app.utils import banco_indisponivel as _ignorar_banco_indisponivel

BASE_URL = IBGE_LOCALIDADES_BASE_URL


def _registrar_falha_banco(acao: str, error: Exception) -> None:
    print(f"Falha ao {acao} no Postgres: {error}")


def _salvar_municipios(municipios: list[dict[str, Any]], uf: str) -> None:
    try:
        database.salvar_municipios_ibge(municipios, uf)
    except RuntimeError as error:
        if _ignorar_banco_indisponivel(error):
            return
        _registrar_falha_banco("salvar municipios do IBGE", error)
    except Exception as error:
        _registrar_falha_banco("salvar municipios do IBGE", error)


def _buscar_municipio_no_banco(codigo_ibge: str | int) -> dict[str, Any] | None:
    try:
        return database.localizar_municipio_ibge(codigo_ibge)
    except RuntimeError as error:
        if _ignorar_banco_indisponivel(error):
            return None
        raise


def buscar_dados_ibge(path: str, params: dict[str, Any] | None = None, max_retries: int = 2) -> Any:
    url = f"{BASE_URL}/{path.lstrip('/')}"
    espera = 0.5

    for tentativa in range(max_retries + 1):
        try:
            response = requests.get(url, params=params or {}, timeout=(5, 20))

            if response.status_code in {429, 500, 502, 503, 504} and tentativa < max_retries:
                time.sleep(espera)
                espera *= 2
                continue

            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if tentativa == max_retries:
                return [] if path.endswith("/municipios") else {}

            time.sleep(espera)
            espera *= 2

    return [] if path.endswith("/municipios") else {}


def listar_municipios(uf: str = "CE") -> list[dict[str, Any]]:
    dados = buscar_dados_ibge(f"estados/{uf}/municipios", {"orderBy": "nome"})
    municipios = dados if isinstance(dados, list) else []

    if municipios:
        _salvar_municipios(municipios, uf)

    return municipios


def localizar_municipio(codigo_ibge: str | int, uf: str = "CE") -> dict[str, Any] | None:
    codigo = str(codigo_ibge)
    municipio_salvo = _buscar_municipio_no_banco(codigo)

    if municipio_salvo is not None:
        return municipio_salvo

    for municipio in listar_municipios(uf):
        if str(municipio.get("id")) == codigo:
            return municipio

    return None


def sincronizar_municipios(uf: str = "CE") -> int:
    dados = buscar_dados_ibge(f"estados/{uf}/municipios", {"orderBy": "nome"})
    municipios = dados if isinstance(dados, list) else []
    return database.salvar_municipios_ibge(municipios, uf)

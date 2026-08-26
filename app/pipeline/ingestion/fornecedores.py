from typing import Any, Iterable
import time
import requests
from app.core.config import OPENCNPJ_BASE_URL
from app.core import database
from app.utils import (
    banco_indisponivel as _ignorar_banco_indisponivel,
    normalizar_texto as _normalizar_texto,
    primeiro_valor as _primeiro_valor,
    somente_digitos,
)

OPENCNPJ_URL = OPENCNPJ_BASE_URL

class FonteCadastralIndisponivelError(RuntimeError):
    """Uma fonte cadastral falhou; nao significa que o CNPJ nao possua dados."""


def normalizar_porte_me(valor: Any) -> str | None:
    texto = _normalizar_texto(valor)
    if texto in {"ME", "MICRO EMPRESA", "MICROEMPRESA"}:
        return "ME"

    return None


_CAMINHOS_PORTE = (
    ("porte", "descricao"),
    ("porte",),
    ("descricao_porte",),
    ("porte_empresa",),
    ("empresa", "porte", "descricao"),
    ("empresa", "porte"),
    ("estabelecimento", "porte", "descricao"),
    ("estabelecimento", "porte"),
)

_CAMINHOS_RAZAO_SOCIAL = (
    ("razao_social",),
    ("razaoSocial",),
    ("nome",),
    ("nome_empresarial",),
    ("empresa", "razao_social"),
    ("empresa", "razaoSocial"),
    ("estabelecimento", "nome_empresarial"),
)


def extrair_porte_cadastral(payload: dict[str, Any]) -> str | None:
    """Extrai o porte informado pela fonte sem inferi-lo de Simples/MEI."""
    valor = _primeiro_valor(payload, _CAMINHOS_PORTE)
    if isinstance(valor, dict) or valor in (None, ""):
        return None
    texto = str(valor).strip()
    return texto or None


def extrair_razao_social(payload: dict[str, Any]) -> str | None:
    valor = _primeiro_valor(payload, _CAMINHOS_RAZAO_SOCIAL)
    if isinstance(valor, dict) or valor in (None, ""):
        return None
    texto = str(valor).strip()
    return texto or None


def _buscar_fornecedor_me_no_banco(cnpj: str) -> dict[str, Any] | None:
    try:
        return database.localizar_fornecedor_me(cnpj)
    except RuntimeError as error:
        if _ignorar_banco_indisponivel(error):
            return None
        raise


def _salvar_fornecedor_me_no_banco(fornecedor: dict[str, Any]) -> None:
    try:
        database.salvar_fornecedor_me(
            fornecedor["cnpj"],
            fornecedor.get("razao_social"),
            fornecedor["porte"],
        )
    except RuntimeError as error:
        if _ignorar_banco_indisponivel(error):
            return
        print(f"Falha ao salvar fornecedor ME no Postgres: {error}")
    except Exception as error:
        print(f"Falha ao salvar fornecedor ME no Postgres: {error}")


def _get_json(url: str, max_retries: int = 2) -> dict[str, Any]:
    espera = 0.5
    ultimo_erro: Exception | None = None

    for tentativa in range(max_retries + 1):
        try:
            response = requests.get(url, timeout=(5, 25))

            if response.status_code == 404:
                return {}

            if response.status_code in {429, 500, 502, 503, 504} and tentativa < max_retries:
                time.sleep(espera)
                espera *= 2
                continue

            response.raise_for_status()
            dados = response.json()
            return dados if isinstance(dados, dict) else {}
        except (requests.RequestException, ValueError) as error:
            ultimo_erro = error
            if tentativa == max_retries:
                raise FonteCadastralIndisponivelError(
                    f"Falha na fonte cadastral apos {max_retries + 1} tentativa(s): {url}"
                ) from ultimo_erro

            time.sleep(espera)
            espera *= 2

    raise FonteCadastralIndisponivelError(f"Falha inesperada na fonte cadastral: {url}")


def buscar_opencnpj(cnpj: str) -> dict[str, Any]:
    cnpj_limpo = somente_digitos(cnpj)
    if len(cnpj_limpo) != 14:
        return {}

    dados = _get_json(f"{OPENCNPJ_URL}/cnpj/{cnpj_limpo}")
    if isinstance(dados.get("data"), dict):
        return dados["data"]

    return dados


def coletar_fornecedor(cnpj: str) -> dict[str, Any]:
    cnpj_limpo = somente_digitos(cnpj)
    if len(cnpj_limpo) != 14:
        return {
            "cnpj": cnpj_limpo,
            "opencnpj": {},
            "razao_social": None,
            "porte": None,
            "porte_fonte": None,
            "opencnpj_status": "cnpj_invalido",
        }

    try:
        opencnpj = buscar_opencnpj(cnpj_limpo)
        status = "ok" if opencnpj else "nao_encontrado"
    except FonteCadastralIndisponivelError:
        opencnpj = {}
        status = "indisponivel"

    porte = extrair_porte_cadastral(opencnpj) if opencnpj else None

    return {
        "cnpj": cnpj_limpo,
        "opencnpj": opencnpj,
        "razao_social": extrair_razao_social(opencnpj) if opencnpj else None,
        "porte": porte,
        "porte_fonte": "opencnpj" if porte else None,
        "opencnpj_status": status,
    }


def extrair_fornecedor_me(dados: dict[str, Any]) -> dict[str, Any] | None:
    cnpj = somente_digitos(dados.get("cnpj"))
    if len(cnpj) != 14:
        return None

    opencnpj = dados.get("opencnpj") if isinstance(dados.get("opencnpj"), dict) else {}

    porte = dados.get("porte") or extrair_porte_cadastral(opencnpj)
    if normalizar_porte_me(porte) != "ME":
        return None

    razao_social = dados.get("razao_social") or extrair_razao_social(opencnpj)

    return {
        "cnpj": cnpj,
        "razao_social": str(razao_social).strip() if razao_social not in (None, "") else None,
        "porte": "ME",
    }


def validar_fornecedor_me(cnpj: str) -> dict[str, Any] | None:
    cnpj_limpo = somente_digitos(cnpj)
    if len(cnpj_limpo) != 14:
        return None

    fornecedor_salvo = _buscar_fornecedor_me_no_banco(cnpj_limpo)
    if fornecedor_salvo is not None:
        return fornecedor_salvo

    fornecedor_me = extrair_fornecedor_me(coletar_fornecedor(cnpj_limpo))
    if fornecedor_me is not None:
        _salvar_fornecedor_me_no_banco(fornecedor_me)

    return fornecedor_me


def coletar_fornecedores_em_lote(
    cnpjs: Iterable[str],
    *,
    throttle_segundos: float = 0.3,
) -> list[dict[str, Any]]:
    """
    Chama `coletar_fornecedor` uma vez para cada CNPJ distinto de `cnpjs`,
    com uma pausa entre chamadas (mesmo padrao de espacamento entre paginas
    ja usado em pncp.py/tce.py) para nao estourar limite de requisicoes das
    APIs publicas. Duplicatas na lista de entrada sao ignoradas — cada CNPJ
    e consultado uma unica vez, mesmo que apareca em varios contratos.

    Use `app.pipeline.merge.extrair_cnpjs_distintos` para montar `cnpjs` a
    partir das tabelas ja limpas de contratos/contratados.
    """
    vistos: set[str] = set()
    resultados: list[dict[str, Any]] = []

    for cnpj in cnpjs:
        cnpj_limpo = somente_digitos(cnpj)
        if not cnpj_limpo or cnpj_limpo in vistos:
            continue
        vistos.add(cnpj_limpo)

        resultados.append(coletar_fornecedor(cnpj_limpo))
        if throttle_segundos:
            time.sleep(throttle_segundos)

    return resultados

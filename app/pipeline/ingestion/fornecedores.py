from datetime import datetime, timedelta, timezone
import json
from typing import Any, Iterable
import time

import pandas as pd
import requests

from app.core.config import OPENCNPJ_BASE_URL
from app.core import database
from app.pipeline.cleaners import opencnpj as opencnpj_cleaning
from app.utils import (
    banco_indisponivel as _ignorar_banco_indisponivel,
    normalizar_cnpj,
    normalizar_texto as _normalizar_texto,
    somente_digitos,
)

OPENCNPJ_URL = OPENCNPJ_BASE_URL
OPENCNPJ_DATASET_RECEITA = "receita"
TTL_CACHE_FORNECEDOR_ENCONTRADO = timedelta(days=30)
TTL_CACHE_FORNECEDOR_NAO_ENCONTRADO = timedelta(days=7)
TTL_CACHE_FONTE_INDISPONIVEL = timedelta(hours=1)


class FonteCadastralIndisponivelError(RuntimeError):
    """Uma fonte cadastral falhou; nao significa que o CNPJ nao possua dados."""


def normalizar_porte_me(valor: Any) -> str | None:
    texto = _normalizar_texto(valor)
    if texto in {
        "ME",
        "MICRO EMPRESA",
        "MICROEMPRESA",
        "MICRO EMPRESA (ME)",
        "MICROEMPRESA (ME)",
    }:
        return "ME"

    return None


def extrair_porte_cadastral(payload: dict[str, Any]) -> str | None:
    """Extrai o porte informado pela fonte sem inferi-lo de Simples/MEI."""
    valor = payload.get("porte_empresa")
    if isinstance(valor, dict) or valor in (None, ""):
        return None
    texto = str(valor).strip()
    return texto or None


def extrair_razao_social(payload: dict[str, Any]) -> str | None:
    valor = payload.get("razao_social")
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


def _get_json(
    url: str,
    params: dict[str, Any] | None = None,
    max_retries: int = 2,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    espera = 0.5
    ultimo_erro: Exception | None = None
    cliente_http = session if session is not None else requests

    for tentativa in range(max_retries + 1):
        try:
            response = cliente_http.get(url, params=params or {}, timeout=(5, 25))

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


def _montar_requisicao_opencnpj(cnpj: str) -> tuple[str, dict[str, str]]:
    base_url = OPENCNPJ_URL.rstrip("/")
    return f"{base_url}/{cnpj}", {"datasets": OPENCNPJ_DATASET_RECEITA}


def buscar_opencnpj(
    cnpj: str,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    cnpj_limpo = normalizar_cnpj(cnpj)
    if not cnpj_limpo:
        return {}

    url, params = _montar_requisicao_opencnpj(cnpj_limpo)
    dados = _get_json(url, params=params, session=session)
    return dados


def coletar_fornecedor(
    cnpj: str,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    cnpj_limpo = normalizar_cnpj(cnpj)
    if not cnpj_limpo:
        return {
            "cnpj": somente_digitos(cnpj),
            "opencnpj": {},
            "razao_social": None,
            "porte": None,
            "porte_fonte": None,
            "opencnpj_status": "cnpj_invalido",
        }

    try:
        opencnpj = buscar_opencnpj(cnpj_limpo, session=session)
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
    cnpj = normalizar_cnpj(dados.get("cnpj"))
    if not cnpj:
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
    cnpj_limpo = normalizar_cnpj(cnpj)
    if not cnpj_limpo:
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
    ja usado nas demais fontes) para nao estourar limite de requisicoes das
    APIs publicas. A pausa ocorre antes de cada chamada a partir da segunda,
    evitando espera desnecessaria depois do ultimo fornecedor. Duplicatas na
    lista de entrada sao ignoradas — cada CNPJ e consultado uma unica vez,
    mesmo que apareca em varios contratos.

    Use `app.pipeline.enrichment.fornecedores.extrair_cnpjs_distintos` para montar `cnpjs` a
    partir das tabelas ja limpas de contratos/contratados.
    """
    vistos: set[str] = set()
    resultados: list[dict[str, Any]] = []

    with requests.Session() as session:
        for cnpj in cnpjs:
            cnpj_limpo = normalizar_cnpj(cnpj)
            if not cnpj_limpo or cnpj_limpo in vistos:
                continue
            vistos.add(cnpj_limpo)

            if resultados and throttle_segundos:
                time.sleep(throttle_segundos)
            resultados.append(coletar_fornecedor(cnpj_limpo, session=session))

    return resultados


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _instante_utc(valor: datetime | str) -> datetime:
    instante = datetime.fromisoformat(valor) if isinstance(valor, str) else valor
    return (
        instante.replace(tzinfo=timezone.utc)
        if instante.tzinfo is None
        else instante.astimezone(timezone.utc)
    )


def _ttl_cache_status(status: str) -> timedelta:
    if status == "ok":
        return TTL_CACHE_FORNECEDOR_ENCONTRADO
    if status == "nao_encontrado":
        return TTL_CACHE_FORNECEDOR_NAO_ENCONTRADO
    return TTL_CACHE_FONTE_INDISPONIVEL


def _valor_json(valor: Any) -> Any:
    if valor is None:
        return None
    if isinstance(valor, dict):
        return {str(chave): _valor_json(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_valor_json(item) for item in valor]
    if isinstance(valor, (datetime, pd.Timestamp)):
        return valor.isoformat()

    try:
        nulo = pd.isna(valor)
        if not isinstance(nulo, (list, tuple)) and not getattr(nulo, "shape", None) and bool(nulo):
            return None
    except (TypeError, ValueError):
        pass

    if hasattr(valor, "item") and not isinstance(valor, (str, bytes, bytearray)):
        try:
            return valor.item()
        except (AttributeError, TypeError, ValueError):
            pass
    return valor


def _registros_normalizados(
    registros: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    dataframe = opencnpj_cleaning.limpar_fornecedores(registros)
    if dataframe.empty:
        return {}

    normalizados: dict[str, dict[str, Any]] = {}
    for registro in dataframe.to_dict(orient="records"):
        registro_json = {str(chave): _valor_json(valor) for chave, valor in registro.items()}
        cnpj = normalizar_cnpj(registro_json.get("cnpj"))
        if cnpj:
            registro_json["cnpj"] = cnpj
            normalizados[cnpj] = registro_json
    return normalizados


def _dados_cache_para_resultado(cache: dict[str, Any]) -> dict[str, Any]:
    dados = dict(cache.get("dados_normalizados") or {})
    dados.setdefault("cnpj", cache["cnpj"])
    dados["cache_desatualizado"] = bool(cache.get("cache_desatualizado"))
    dados["cache_ultima_tentativa_status"] = cache.get("ultima_tentativa_status")
    return dados


def obter_fornecedores_com_cache(
    cnpjs: Iterable[str],
    *,
    throttle_segundos: float = 0.3,
) -> pd.DataFrame:
    """Obtém dados normalizados, consultando o OpenCNPJ apenas em cache miss/expiração."""
    cnpjs_normalizados: list[str] = []
    vistos: set[str] = set()
    for cnpj in cnpjs:
        cnpj_limpo = normalizar_cnpj(cnpj)
        if cnpj_limpo and cnpj_limpo not in vistos:
            vistos.add(cnpj_limpo)
            cnpjs_normalizados.append(cnpj_limpo)

    if not cnpjs_normalizados:
        return pd.DataFrame()

    agora = _agora_utc()
    cache_por_cnpj = database.listar_fornecedores_cache(cnpjs_normalizados)
    resultados: dict[str, dict[str, Any]] = {}
    cnpjs_para_atualizar: list[str] = []

    for cnpj in cnpjs_normalizados:
        cache = cache_por_cnpj.get(cnpj)
        if cache is not None and _instante_utc(cache["expira_em"]) > agora:
            resultados[cnpj] = _dados_cache_para_resultado(cache)
        else:
            cnpjs_para_atualizar.append(cnpj)

    if cnpjs_para_atualizar:
        registros_brutos = coletar_fornecedores_em_lote(
            cnpjs_para_atualizar,
            throttle_segundos=throttle_segundos,
        )
        brutos_por_cnpj = {
            cnpj: registro
            for registro in registros_brutos
            if (cnpj := normalizar_cnpj(registro.get("cnpj")))
        }
        normalizados_por_cnpj = _registros_normalizados(registros_brutos)
        registros_cache: list[dict[str, Any]] = []

        for cnpj in cnpjs_para_atualizar:
            bruto = brutos_por_cnpj.get(
                cnpj,
                {"cnpj": cnpj, "opencnpj": {}, "opencnpj_status": "indisponivel"},
            )
            status = str(bruto.get("opencnpj_status") or "indisponivel")
            cache_anterior = cache_por_cnpj.get(cnpj)

            if (
                status == "indisponivel"
                and cache_anterior is not None
                and cache_anterior.get("opencnpj_status") in {"ok", "nao_encontrado"}
                and cache_anterior.get("dados_normalizados")
            ):
                cache_atualizado = {
                    **cache_anterior,
                    "expira_em": agora + TTL_CACHE_FONTE_INDISPONIVEL,
                    "ultima_tentativa_em": agora,
                    "ultima_tentativa_status": "indisponivel",
                    "cache_desatualizado": True,
                    "atualizado_em": agora,
                }
                registros_cache.append(cache_atualizado)
                resultados[cnpj] = _dados_cache_para_resultado(cache_atualizado)
                continue

            dados_normalizados = normalizados_por_cnpj.get(
                cnpj,
                {"cnpj": cnpj, "opencnpj_status": status},
            )
            payload = bruto.get("opencnpj") if isinstance(bruto.get("opencnpj"), dict) else {}
            novo_cache = {
                "cnpj": cnpj,
                "opencnpj_status": status,
                "dados_normalizados": dados_normalizados,
                "payload": json.loads(json.dumps(payload, ensure_ascii=False, default=str)),
                "observado_em": agora,
                "expira_em": agora + _ttl_cache_status(status),
                "ultima_tentativa_em": agora,
                "ultima_tentativa_status": status,
                "cache_desatualizado": False,
                "atualizado_em": agora,
            }
            registros_cache.append(novo_cache)
            resultados[cnpj] = _dados_cache_para_resultado(novo_cache)

        database.salvar_fornecedores_cache(registros_cache)

    return pd.DataFrame(
        [resultados[cnpj] for cnpj in cnpjs_normalizados if cnpj in resultados]
    )

from typing import Any
import time
import json
import requests

from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, TCE_CE_BASE_URL
from app.pipeline.ingestion.pagination import LIMITE_REGISTROS_POR_REQUISICAO, listar_por_start_index
from app.utils import normalizar_data

BASE_URL = TCE_CE_BASE_URL
TAMANHO_PAGINA = LIMITE_REGISTROS_POR_REQUISICAO
CODIGO_MUNICIPIO_PADRAO = CODIGO_MUNICIPIO_TCE_PADRAO

ENDPOINT_CONTRATACOES = "processos_administrativos_contratacoes"
ENDPOINT_CONTRATOS = "contratos"
ENDPOINT_CONTRATADOS = "contratados"
ENDPOINT_ITENS = "itens_compoem_bens_servicos"


def normalizar_data_tce(data: str) -> str:
    return normalizar_data(data, ("%Y-%m-%d", "%Y%m%d"), "%Y-%m-%d", "YYYY-MM-DD ou YYYYMMDD")


def buscar_dados_tce(endpoint: str, params: dict[str, Any], max_retries: int = 3) -> dict[str, Any]:
    url = f"{BASE_URL}/{endpoint}"
    params = {**params, "$format": "json"}
    espera = 1.0

    for tentativa in range(max_retries + 1):
        try:
            response = requests.get(url, params=params, timeout=(10, 30))

            if response.status_code == 204:
                return {"elements": []}

            if response.status_code in {429, 500, 502, 503, 504} and tentativa < max_retries:
                time.sleep(espera)
                espera *= 2
                continue

            response.raise_for_status()
            dados = response.json()
            return dados if isinstance(dados, dict) else {"elements": dados}

        except (requests.RequestException, ValueError) as error:
            if tentativa == max_retries:
                print(f"Falha ao buscar dados do TCE-CE: {error}")
                return {"elements": []}

            time.sleep(espera)
            espera *= 2

    return {"elements": []}


def listar_registros(endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    def buscar_pagina(start_index: int, tamanho_pagina: int) -> list[Any]:
        pagina = buscar_dados_tce(
            endpoint,
            {
                **params,
                "$count": tamanho_pagina,
                "$start_index": start_index,
            },
        ).get("elements", [])
        return pagina if isinstance(pagina, list) else []

    return listar_por_start_index(buscar_pagina, tamanho_pagina=TAMANHO_PAGINA)


def buscar_municipios() -> list[dict[str, Any]]:
    return listar_registros("municipios", {})


def _params_periodo(data_inicial: str, data_final: str, codigo_municipio: str) -> dict[str, str]:
    return {
        "codigo_municipio": codigo_municipio,
        "data_inicio": normalizar_data_tce(data_inicial),
        "data_fim": normalizar_data_tce(data_final),
    }


def buscar_contratacoes(
    data_inicial: str,
    data_final: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
    modalidade: str = "",
) -> list[dict[str, Any]]:
    registros = listar_registros(
        ENDPOINT_CONTRATACOES,
        _params_periodo(data_inicial, data_final, codigo_municipio),
    )

    if not modalidade:
        return registros

    return [registro for registro in registros if registro.get("modalidade_licitacao") == modalidade]


def buscar_contratos(
    data_inicial: str,
    data_final: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
) -> list[dict[str, Any]]:
    return listar_registros(
        ENDPOINT_CONTRATOS,
        _params_periodo(data_inicial, data_final, codigo_municipio),
    )


def buscar_contratados(
    data_inicial: str,
    data_final: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
) -> list[dict[str, Any]]:
    return listar_registros(
        ENDPOINT_CONTRATADOS,
        _params_periodo(data_inicial, data_final, codigo_municipio),
    )


def buscar_itens_contratacao(
    data_inicial: str,
    data_final: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
    numero_licitacao: str = "",
) -> list[dict[str, Any]]:
    registros = listar_registros(
        ENDPOINT_ITENS,
        _params_periodo(data_inicial, data_final, codigo_municipio),
    )

    if not numero_licitacao:
        return registros

    return [registro for registro in registros if registro.get("numero_licitacao") == numero_licitacao]


if __name__ == "__main__":
    contratos = buscar_contratos("20241001", "20250101")
    print(f"Total de contratos em Amontada: {len(contratos)}")
    print(json.dumps(contratos, indent=2, ensure_ascii=False))

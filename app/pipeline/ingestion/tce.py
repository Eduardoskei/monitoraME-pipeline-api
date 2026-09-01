from typing import Any
import logging
import time
import json
import requests

from app.core.logging import executar_com_log_ingestao, registrar_falha_ingestao
from app.core.config import (
    CODIGO_MUNICIPIO_TCE_PADRAO,
    NATUREZAS_DESPESA_CONSIDERADAS,
    TCE_CE_BASE_URL,
)
from app.pipeline.ingestion.pagination import LIMITE_REGISTROS_POR_REQUISICAO, listar_por_start_index
from app.utils import normalizar_data, normalizar_texto, primeiro_valor

BASE_URL = TCE_CE_BASE_URL
TAMANHO_PAGINA = LIMITE_REGISTROS_POR_REQUISICAO
CODIGO_MUNICIPIO_PADRAO = CODIGO_MUNICIPIO_TCE_PADRAO

logger = logging.getLogger(__name__)

ENDPOINT_CONTRATACOES = "processos_administrativos_contratacoes"
ENDPOINT_CONTRATOS = "contratos"
ENDPOINT_CONTRATADOS = "contratados"
ENDPOINT_ITENS = "itens_compoem_bens_servicos"

_CAMINHOS_NATUREZA_DESPESA = (
    ("natureza_despesa",),
    ("descricao_natureza_despesa",),
    ("nome_natureza_despesa",),
    ("naturezaDespesa",),
)
_NATUREZAS_DESPESA_NORMALIZADAS = {
    normalizar_texto(natureza) for natureza in NATUREZAS_DESPESA_CONSIDERADAS
}


def normalizar_data_tce(data: str) -> str:
    return normalizar_data(data, ("%Y-%m-%d", "%Y%m%d"), "%Y-%m-%d", "YYYY-MM-DD ou YYYYMMDD")


def filtrar_naturezas_despesa(registros: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Descarta registros com natureza informada fora do escopo configurado.

    Endpoints que nao fornecem natureza de despesa permanecem intactos. Isso
    permite aplicar a regra na fronteira da ingestao sem eliminar contratos e
    entidades cujo schema nao possui esse atributo.
    """
    filtrados: list[dict[str, Any]] = []
    for registro in registros:
        natureza = primeiro_valor(registro, _CAMINHOS_NATUREZA_DESPESA)
        if natureza is None or normalizar_texto(natureza) in _NATUREZAS_DESPESA_NORMALIZADAS:
            filtrados.append(registro)
    return filtrados


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
                registrar_falha_ingestao(error)
                logger.warning("Falha ao buscar dados do TCE-CE: %s", error)
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

    registros = listar_por_start_index(buscar_pagina, tamanho_pagina=TAMANHO_PAGINA)
    return filtrar_naturezas_despesa(registros)


def buscar_municipios() -> list[dict[str, Any]]:
    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_municipios",
        parametros={},
        totais={"endpoint": "municipios"},
        executar=lambda: listar_registros("municipios", {}),
    )


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
    parametros = {
        "data_inicial": data_inicial,
        "data_final": data_final,
        "codigo_municipio": codigo_municipio,
        "modalidade": modalidade,
    }

    def executar() -> list[dict[str, Any]]:
        registros = listar_registros(
            ENDPOINT_CONTRATACOES,
            _params_periodo(data_inicial, data_final, codigo_municipio),
        )

        if not modalidade:
            return registros

        return [registro for registro in registros if registro.get("modalidade_licitacao") == modalidade]

    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_contratacoes",
        parametros=parametros,
        totais={"endpoint": ENDPOINT_CONTRATACOES},
        executar=executar,
    )


def buscar_contratos(
    data_inicial: str,
    data_final: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
) -> list[dict[str, Any]]:
    parametros = {
        "data_inicial": data_inicial,
        "data_final": data_final,
        "codigo_municipio": codigo_municipio,
    }
    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_contratos",
        parametros=parametros,
        totais={"endpoint": ENDPOINT_CONTRATOS},
        executar=lambda: listar_registros(
            ENDPOINT_CONTRATOS,
            _params_periodo(data_inicial, data_final, codigo_municipio),
        ),
    )


def buscar_contratados(
    data_inicial: str,
    data_final: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
) -> list[dict[str, Any]]:
    parametros = {
        "data_inicial": data_inicial,
        "data_final": data_final,
        "codigo_municipio": codigo_municipio,
    }
    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_contratados",
        parametros=parametros,
        totais={"endpoint": ENDPOINT_CONTRATADOS},
        executar=lambda: listar_registros(
            ENDPOINT_CONTRATADOS,
            _params_periodo(data_inicial, data_final, codigo_municipio),
        ),
    )


def buscar_itens_contratacao(
    data_inicial: str,
    data_final: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
    numero_licitacao: str = "",
) -> list[dict[str, Any]]:
    parametros = {
        "data_inicial": data_inicial,
        "data_final": data_final,
        "codigo_municipio": codigo_municipio,
        "numero_licitacao": numero_licitacao,
    }

    def executar() -> list[dict[str, Any]]:
        registros = listar_registros(
            ENDPOINT_ITENS,
            _params_periodo(data_inicial, data_final, codigo_municipio),
        )

        if not numero_licitacao:
            return registros

        return [registro for registro in registros if registro.get("numero_licitacao") == numero_licitacao]

    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_itens_contratacao",
        parametros=parametros,
        totais={"endpoint": ENDPOINT_ITENS},
        executar=executar,
    )

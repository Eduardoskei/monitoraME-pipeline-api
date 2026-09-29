from decimal import Decimal
import logging
import time
from typing import Any

import requests

from app.core.logging import executar_com_log_ingestao, registrar_falha_ingestao
from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, TCE_CE_BASE_URL
from app.pipeline.ingestion.pagination import LIMITE_REGISTROS_POR_REQUISICAO, listar_por_start_index
from app.pipeline.naturezas_despesa import codigo_natureza_despesa_monitorado
from app.utils import normalizar_data_iso

BASE_URL = TCE_CE_BASE_URL
TAMANHO_PAGINA = LIMITE_REGISTROS_POR_REQUISICAO
CODIGO_MUNICIPIO_PADRAO = CODIGO_MUNICIPIO_TCE_PADRAO

logger = logging.getLogger(__name__)


class TceIndisponivelError(RuntimeError):
    """A fonte TCE-CE falhou; uma resposta vazia não pode ser presumida."""

ENDPOINT_CONTRATACOES = "processos_administrativos_contratacoes"
ENDPOINT_CONTRATOS = "contratos"
ENDPOINT_CONTRATADOS = "contratados"
ENDPOINT_ITENS = "itens_compoem_bens_servicos"
ENDPOINT_NOTAS_EMPENHOS = "notas_empenhos"
ENDPOINT_NOTAS_ANULACOES_EMPENHOS = "notas_anulacoes_empenhos"


def normalizar_data_tce(data: str) -> str:
    return normalizar_data_iso(data, "%Y-%m-%d")


def filtrar_naturezas_despesa(registros: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Descarta registros cujo codigo de natureza/elemento nao esta no escopo."""
    filtrados: list[dict[str, Any]] = []
    for registro in registros:
        codigo = registro.get("codigo_elemento_despesa")
        if codigo_natureza_despesa_monitorado(codigo):
            filtrados.append(registro)
    return filtrados


def buscar_dados_tce(
    endpoint: str,
    params: dict[str, Any],
    max_retries: int = 3,
    *,
    preservar_decimais: bool = False,
    falhar_ao_esgotar: bool = False,
) -> dict[str, Any]:
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
            dados = response.json(parse_float=Decimal) if preservar_decimais else response.json()
            return dados if isinstance(dados, dict) else {"elements": dados}

        except (requests.RequestException, ValueError) as error:
            if tentativa == max_retries:
                registrar_falha_ingestao(error)
                logger.warning("Falha ao buscar dados do TCE-CE: %s", error)
                if falhar_ao_esgotar:
                    raise TceIndisponivelError(
                        f"TCE-CE indisponível após {max_retries + 1} tentativa(s): {endpoint}."
                    ) from error
                return {"elements": []}

            time.sleep(espera)
            espera *= 2

    return {"elements": []}


def listar_registros(
    endpoint: str,
    params: dict[str, Any],
    *,
    filtrar_por_codigo_despesa: bool = False,
    preservar_decimais: bool = False,
    falhar_ao_esgotar: bool = False,
) -> list[dict[str, Any]]:
    def buscar_pagina(start_index: int, tamanho_pagina: int) -> list[Any]:
        opcoes_requisicao: dict[str, bool] = {}
        if preservar_decimais:
            opcoes_requisicao["preservar_decimais"] = True
        if falhar_ao_esgotar:
            opcoes_requisicao["falhar_ao_esgotar"] = True
        pagina = buscar_dados_tce(
            endpoint,
            {
                **params,
                "$count": tamanho_pagina,
                "$start_index": start_index,
            },
            **opcoes_requisicao,
        ).get("elements", [])
        return pagina if isinstance(pagina, list) else []

    registros = listar_por_start_index(buscar_pagina, tamanho_pagina=TAMANHO_PAGINA)
    if not filtrar_por_codigo_despesa:
        return registros
    return filtrar_naturezas_despesa(registros)


def buscar_municipios() -> list[dict[str, Any]]:
    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_municipios",
        parametros={},
        totais={"endpoint": "municipios"},
        executar=lambda: listar_registros("municipios", {}, filtrar_por_codigo_despesa=False),
    )


def _params_periodo(data_inicial: str, data_final: str, codigo_municipio: str) -> dict[str, str]:
    return {
        "codigo_municipio": codigo_municipio,
        "data_inicio": normalizar_data_tce(data_inicial),
        "data_fim": normalizar_data_tce(data_final),
    }


def parametros_competencia_tce(competencia: str, codigo_municipio: str) -> dict[str, str | int]:
    """Converte ``YYYY-MM`` para os parâmetros exigidos pelas tabelas DCD do SIM."""
    if not isinstance(competencia, str):
        raise TypeError(f"Competencia deve ser str, nao {type(competencia).__name__}.")

    texto = competencia.strip()
    if len(texto) != 7 or texto[4] != "-" or not (texto[:4] + texto[5:]).isdigit():
        raise ValueError(f"Competencia invalida: {competencia!r}. Use YYYY-MM.")

    ano = int(texto[:4])
    mes = int(texto[5:])
    if ano < 1 or mes < 1 or mes > 12:
        raise ValueError(f"Competencia invalida: {competencia!r}. Use YYYY-MM.")

    municipio = str(codigo_municipio).strip()
    if not municipio:
        raise ValueError("codigo_municipio nao pode ser vazio.")

    return {
        "codigo_municipio": municipio,
        "exercicio_orcamento": ano * 100,
        "data_referencia_doc": ano * 100 + mes,
    }


def _buscar_documentos_dcd(
    endpoint: str,
    etapa: str,
    competencia: str,
    codigo_municipio: str,
) -> list[dict[str, Any]]:
    parametros_api = parametros_competencia_tce(competencia, codigo_municipio)
    parametros_log = {
        "competencia": competencia,
        "codigo_municipio": str(codigo_municipio).strip(),
        "exercicio_orcamento": parametros_api["exercicio_orcamento"],
        "data_referencia_doc": parametros_api["data_referencia_doc"],
    }
    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa=etapa,
        parametros=parametros_log,
        totais={"endpoint": endpoint},
        executar=lambda: listar_registros(
            endpoint,
            parametros_api,
            filtrar_por_codigo_despesa=False,
            preservar_decimais=True,
            falhar_ao_esgotar=True,
        ),
    )


def buscar_notas_empenhos(
    competencia: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
) -> list[dict[str, Any]]:
    """Coleta todas as notas de empenho de um município em uma competência."""
    return _buscar_documentos_dcd(
        ENDPOINT_NOTAS_EMPENHOS,
        "buscar_notas_empenhos",
        competencia,
        codigo_municipio,
    )


def buscar_notas_anulacoes_empenhos(
    competencia: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
) -> list[dict[str, Any]]:
    """Coleta todas as anulações de empenho de um município em uma competência."""
    return _buscar_documentos_dcd(
        ENDPOINT_NOTAS_ANULACOES_EMPENHOS,
        "buscar_notas_anulacoes_empenhos",
        competencia,
        codigo_municipio,
    )


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
            filtrar_por_codigo_despesa=False,
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
            filtrar_por_codigo_despesa=False,
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

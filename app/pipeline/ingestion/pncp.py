from dataclasses import dataclass
from typing import Any
import time
import requests

from app.core.config import PNCP_CONSULTA_BASE_URL, PNCP_GESTAO_BASE_URL
from app.utils import (
    filtrar_params_vazios,
    normalizar_data,
    primeiro_valor as _primeiro_valor,
    somente_digitos,
)

CONSULTA_BASE_URL = PNCP_CONSULTA_BASE_URL
GESTAO_BASE_URL = PNCP_GESTAO_BASE_URL
TAMANHO_PAGINA_CONTRATACOES = 50
TAMANHO_PAGINA_DETALHES = 500


class PncpIndisponivelError(RuntimeError):
    """A consulta ao PNCP falhou; nao equivale a uma consulta sem resultados."""


@dataclass(frozen=True)
class IdentificadorCompra:
    cnpj_orgao: str
    ano_compra: int
    sequencial_compra: int


def normalizar_data_pncp(data: str) -> str:
    return normalizar_data(data, ("%Y%m%d", "%Y-%m-%d"), "%Y%m%d", "YYYYMMDD ou YYYY-MM-DD")


def _get_json(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    base_url: str = CONSULTA_BASE_URL,
    max_retries: int = 3,
) -> Any:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    espera = 1.0

    ultimo_erro: Exception | None = None
    for tentativa in range(max_retries + 1):
        try:
            response = requests.get(url, params=filtrar_params_vazios(params or {}), timeout=(10, 40))

            if response.status_code == 204:
                return []

            if response.status_code in {404}:
                return []

            if response.status_code in {429, 500, 502, 503, 504} and tentativa < max_retries:
                time.sleep(espera)
                espera *= 2
                continue

            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            ultimo_erro = error
            if tentativa == max_retries:
                raise PncpIndisponivelError(
                    f"Falha ao consultar o PNCP apos {max_retries + 1} tentativa(s): {url}"
                ) from ultimo_erro

            time.sleep(espera)
            espera *= 2

    raise PncpIndisponivelError(f"Falha inesperada ao consultar o PNCP: {url}")


def _numero_item(item: dict[str, Any]) -> int | None:
    numero = _primeiro_valor(item, (("numeroItem",), ("numero_item",), ("itemNumero",)))
    try:
        return int(numero)
    except (TypeError, ValueError):
        return None


def _identificador_contrato(contrato: dict[str, Any]) -> tuple[int, int] | None:
    ano = _primeiro_valor(contrato, (("anoContrato",), ("ano",)))
    sequencial = _primeiro_valor(contrato, (("sequencialContrato",), ("sequencial",)))

    try:
        return int(ano), int(sequencial)
    except (TypeError, ValueError):
        return None


def _extrair_lista(dados: Any) -> list[dict[str, Any]]:
    if isinstance(dados, list):
        return [item for item in dados if isinstance(item, dict)]

    if not isinstance(dados, dict):
        return []

    for chave in (
        "data",
        "content",
        "items",
        "itens",
        "contratacoes",
        "contratos",
        "listaResultados",
        "resultado",
    ):
        valor = dados.get(chave)
        if isinstance(valor, list):
            return [item for item in valor if isinstance(item, dict)]

    return []


def _total_paginas(dados: Any) -> int | None:
    if not isinstance(dados, dict):
        return None

    for chave in ("totalPaginas", "total_pages", "totalPagina", "totalDePaginas"):
        valor = dados.get(chave)
        if isinstance(valor, int):
            return valor
        if isinstance(valor, str) and valor.isdigit():
            return int(valor)

    return None


def _listar_paginas(
    path: str,
    params: dict[str, Any],
    *,
    base_url: str = CONSULTA_BASE_URL,
    tamanho_pagina: int = TAMANHO_PAGINA_CONTRATACOES,
    max_paginas: int | None = None,
) -> list[dict[str, Any]]:
    registros: list[dict[str, Any]] = []
    pagina = 1

    while True:
        dados = _get_json(
            path,
            {
                **params,
                "pagina": pagina,
                "tamanhoPagina": tamanho_pagina,
            },
            base_url=base_url,
        )
        itens = _extrair_lista(dados)

        if not itens:
            break

        registros.extend(itens)

        total_paginas = _total_paginas(dados)
        if total_paginas is not None and pagina >= total_paginas:
            break

        if len(itens) < tamanho_pagina:
            break

        if max_paginas is not None and pagina >= max_paginas:
            break

        pagina += 1
        time.sleep(0.2)

    return registros


def buscar_modalidades() -> list[dict[str, Any]]:
    return _extrair_lista(_get_json("/v1/modalidades"))


def buscar_modalidade(modalidade_id: int = 6) -> dict[str, Any] | None:
    for modalidade in buscar_modalidades():
        codigo = _primeiro_valor(
            modalidade,
            (
                ("modalidadeId",),
                ("id",),
                ("codigo",),
                ("codigoModalidadeContratacao",),
            ),
        )
        if str(codigo) == str(modalidade_id):
            return modalidade

    return None


def buscar_contratacoes_publicadas(
    data_inicial: str,
    data_final: str,
    *,
    modalidade_id: int = 6,
    uf: str | None = "CE",
    codigo_municipio_ibge: str | int | None = None,
    cnpj_orgao: str | None = None,
    max_paginas: int | None = None,
) -> list[dict[str, Any]]:
    params = {
        "dataInicial": normalizar_data_pncp(data_inicial),
        "dataFinal": normalizar_data_pncp(data_final),
        "codigoModalidadeContratacao": modalidade_id,
        "uf": uf,
        "codigoMunicipioIbge": codigo_municipio_ibge,
        "cnpj": somente_digitos(cnpj_orgao),
    }
    return _listar_paginas(
        "/v1/contratacoes/publicacao",
        params,
        tamanho_pagina=TAMANHO_PAGINA_CONTRATACOES,
        max_paginas=max_paginas,
    )


def extrair_identificador_compra(registro: dict[str, Any]) -> IdentificadorCompra | None:
    cnpj = somente_digitos(
        _primeiro_valor(
            registro,
            (
                ("orgaoEntidade", "cnpj"),
                ("orgao", "cnpj"),
                ("cnpjOrgao",),
                ("cnpj",),
                ("cnpjOrgaoEntidade",),
            ),
        )
    )
    ano = _primeiro_valor(registro, (("anoCompra",), ("ano",), ("anoContratacao",)))
    sequencial = _primeiro_valor(
        registro,
        (
            ("sequencialCompra",),
            ("sequencial",),
            ("sequencialContratacao",),
        ),
    )

    if len(cnpj) != 14 or ano in (None, "") or sequencial in (None, ""):
        return None

    try:
        return IdentificadorCompra(cnpj, int(ano), int(sequencial))
    except (TypeError, ValueError):
        return None


def consultar_compra(cnpj: str, ano: int, sequencial: int) -> dict[str, Any]:
    dados = _get_json(
        f"/v1/orgaos/{somente_digitos(cnpj)}/compras/{ano}/{sequencial}",
        base_url=CONSULTA_BASE_URL,
    )
    return dados if isinstance(dados, dict) else {}


def consultar_itens_compra(cnpj: str, ano: int, sequencial: int) -> list[dict[str, Any]]:
    return _listar_paginas(
        f"/v1/orgaos/{somente_digitos(cnpj)}/compras/{ano}/{sequencial}/itens",
        {},
        base_url=GESTAO_BASE_URL,
        tamanho_pagina=TAMANHO_PAGINA_DETALHES,
    )


def consultar_resultados_item(
    cnpj: str,
    ano: int,
    sequencial: int,
    numero_item: int,
) -> list[dict[str, Any]]:
    dados = _get_json(
        f"/v1/orgaos/{somente_digitos(cnpj)}/compras/{ano}/{sequencial}/itens/{numero_item}/resultados",
        base_url=GESTAO_BASE_URL,
    )
    return _extrair_lista(dados)


def consultar_contratos_compra(cnpj: str, ano_compra: int, sequencial_compra: int) -> list[dict[str, Any]]:
    dados = _get_json(
        f"/v1/orgaos/{somente_digitos(cnpj)}/contratos/contratacao/{ano_compra}/{sequencial_compra}",
        base_url=GESTAO_BASE_URL,
    )
    return _extrair_lista(dados)


def detalhar_contrato(cnpj: str, ano_contrato: int, sequencial_contrato: int) -> dict[str, Any]:
    dados = _get_json(
        f"/v1/orgaos/{somente_digitos(cnpj)}/contratos/{ano_contrato}/{sequencial_contrato}",
        base_url=GESTAO_BASE_URL,
    )
    return dados if isinstance(dados, dict) else {}


def coletar_compra_completa(cnpj: str, ano: int, sequencial: int) -> dict[str, Any]:
    cnpj_limpo = somente_digitos(cnpj)
    compra = consultar_compra(cnpj_limpo, ano, sequencial)
    itens = consultar_itens_compra(cnpj_limpo, ano, sequencial)
    contratos = consultar_contratos_compra(cnpj_limpo, ano, sequencial)

    itens_com_resultados = []
    for item in itens:
        numero_item = _numero_item(item)
        resultados = []
        if numero_item is not None:
            resultados = consultar_resultados_item(cnpj_limpo, ano, sequencial, numero_item)
        itens_com_resultados.append(
            {
                "item": item,
                "resultados": resultados,
            }
        )

    contratos_com_detalhes = []
    for contrato in contratos:
        identificador_contrato = _identificador_contrato(contrato)
        detalhe = {}
        if identificador_contrato is not None:
            detalhe = detalhar_contrato(cnpj_limpo, *identificador_contrato)
        contratos_com_detalhes.append(
            {
                "contrato": contrato,
                "detalhe": detalhe,
            }
        )

    return {
        "identificador": {
            "cnpj_orgao": cnpj_limpo,
            "ano_compra": ano,
            "sequencial_compra": sequencial,
        },
        "compra": compra,
        "itens": itens_com_resultados,
        "contratos": contratos_com_detalhes,
    }


def coletar_compras_publicadas(
    data_inicial: str,
    data_final: str,
    *,
    modalidade_id: int = 6,
    uf: str | None = "CE",
    codigo_municipio_ibge: str | int | None = None,
    cnpj_orgao: str | None = None,
    max_paginas: int | None = None,
    incluir_detalhes: bool = False,
) -> list[dict[str, Any]]:
    publicacoes = buscar_contratacoes_publicadas(
        data_inicial,
        data_final,
        modalidade_id=modalidade_id,
        uf=uf,
        codigo_municipio_ibge=codigo_municipio_ibge,
        cnpj_orgao=cnpj_orgao,
        max_paginas=max_paginas,
    )

    if not incluir_detalhes:
        return [{"publicacao": publicacao} for publicacao in publicacoes]

    registros = []
    for publicacao in publicacoes:
        identificador = extrair_identificador_compra(publicacao)
        detalhe = {}
        if identificador is not None:
            detalhe = coletar_compra_completa(
                identificador.cnpj_orgao,
                identificador.ano_compra,
                identificador.sequencial_compra,
            )
        registros.append(
            {
                "publicacao": publicacao,
                "detalhe": detalhe,
            }
        )

    return registros

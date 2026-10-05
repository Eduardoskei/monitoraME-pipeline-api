from __future__ import annotations

from decimal import Decimal, InvalidOperation
from math import ceil
import re
from typing import Any

from app.core.config import NATUREZAS_DESPESA_MONITORADAS
from app.pipeline import empenhos
from app.pipeline.persistence import tce_despesas, tce_fornecedores


TIPOS_DOCUMENTO = {"CNPJ": "1", "CPF": "2"}
TIPOS_DOCUMENTO_PUBLICOS = {valor: chave for chave, valor in TIPOS_DOCUMENTO.items()}


def _documento(tipo_documento: str, valor: str) -> tuple[str, str]:
    normalizado = re.sub(r"\D", "", valor or "")
    tamanho = 14 if tipo_documento == "CNPJ" else 11
    if len(normalizado) != tamanho:
        raise empenhos.EmpenhoRequestError(
            "INVALID_SUPPLIER_DOCUMENT",
            f"documento deve possuir {tamanho} digitos para {tipo_documento}.",
            [{
                "field": "documento",
                "reason": "invalid_length",
                "value": valor,
            }],
        )
    return TIPOS_DOCUMENTO[tipo_documento], normalizado


def _filtros(
    *,
    start_date: str,
    end_date: str,
    uf: str,
    municipality_tce_code: str | None,
    company_sizes: list[str],
    expense_element_codes: list[str],
) -> tuple[Any, Any, str | None, list[str], list[str]]:
    inicio, fim, municipio, portes, elementos = empenhos.preparar_filtros(
        start_date=start_date,
        end_date=end_date,
        uf=uf,
        municipality_tce_code=municipality_tce_code,
        company_sizes=company_sizes,
        expense_element_codes=expense_element_codes,
    )
    codigo_tce = municipio["codigo_municipio_tce"] if municipio else None
    return inicio, fim, codigo_tce, portes, elementos


def _paginacao(total: int, pagina: int, tamanho: int) -> dict[str, Any]:
    total_paginas = ceil(total / tamanho) if total else 0
    return {
        "page": pagina,
        "page_size": tamanho,
        "total_items": total,
        "total_pages": total_paginas,
        "has_previous": pagina > 1,
        "has_next": pagina < total_paginas,
    }


def _tipo_publico(valor: str) -> str:
    return TIPOS_DOCUMENTO_PUBLICOS.get(str(valor), "OUTRO")


def _item_fornecedor(registro: dict[str, Any]) -> dict[str, Any]:
    return {
        "tipo_documento": _tipo_publico(registro["tipo_documento_fornecedor"]),
        "documento": registro["documento_fornecedor"],
        "cnpj": registro["cnpj_fornecedor"],
        "nome": registro["nome_fornecedor"],
        "porte": registro["porte_fornecedor"],
        "cadastro_status": registro["cadastro_status"],
        "origens_no_recorte": registro["origens_no_recorte"],
        "valores": {
            "valor_empenhado_centavos": registro["valor_empenhado_centavos"],
            "valor_anulado_centavos": registro["valor_anulado_centavos"],
            "valor_considerado_centavos": registro["valor_liquido_centavos"],
        },
        "quantidade_empenhos": registro["quantidade_empenhos"],
        "quantidade_anulacoes": registro["quantidade_anulacoes"],
        "quantidade_municipios": registro["quantidade_municipios"],
        "primeiro_empenho": registro["primeiro_empenho"],
        "ultimo_empenho": registro["ultimo_empenho"],
    }


def listar_fornecedores(
    *,
    start_date: str,
    end_date: str,
    uf: str,
    municipality_tce_code: str | None,
    company_sizes: list[str],
    supplier_origins: list[str],
    expense_element_codes: list[str],
    search: str | None,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
) -> dict[str, Any]:
    inicio, fim, codigo_tce, portes, elementos = _filtros(
        start_date=start_date,
        end_date=end_date,
        uf=uf,
        municipality_tce_code=municipality_tce_code,
        company_sizes=company_sizes,
        expense_element_codes=expense_element_codes,
    )
    resultado = tce_fornecedores.listar_fornecedores_publicados_paginados(
        data_inicial=inicio,
        data_final=fim,
        codigo_municipio_tce=codigo_tce,
        portes=portes,
        origens=supplier_origins,
        elementos=elementos,
        busca=search,
        pagina=page,
        tamanho_pagina=page_size,
        ordenar_por=sort_by,
        ordem=sort_order,
    )
    total = int(resultado["total"])
    return {
        "source": "TCE-CE",
        "data_source": "LOCAL_DATABASE",
        "external_requests": False,
        "filters": {
            "uf": uf.upper(),
            "municipality_tce_code": municipality_tce_code,
            "start_date": start_date,
            "end_date": end_date,
            "company_sizes": company_sizes,
            "supplier_origins": supplier_origins,
            "expense_element_codes": expense_element_codes,
            "search": search,
        },
        "pagination": _paginacao(total, page, page_size),
        "sorting": {"sort_by": sort_by, "sort_order": sort_order},
        "items": [_item_fornecedor(item) for item in resultado["registros"]],
    }


def _primeiro(dados: dict[str, Any], *campos: str) -> Any:
    marcadores_ausentes = {"", "-", "nao_informado", "não informado", "null", "none"}
    for campo in campos:
        valor = dados.get(campo)
        if valor in (None, [], {}):
            continue
        if isinstance(valor, str) and valor.strip().lower() in marcadores_ausentes:
            continue
        if valor != "":
            return valor
    return None


def _capital_centavos(valor: Any) -> int | None:
    if valor in (None, ""):
        return None
    try:
        return int(Decimal(str(valor).replace(",", ".")) * 100)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _booleano_cadastral(valor: Any) -> bool | None:
    if isinstance(valor, bool):
        return valor
    normalizado = str(valor or "").strip().lower()
    if normalizado in {"sim", "s", "true", "1"}:
        return True
    if normalizado in {"nao", "não", "n", "false", "0"}:
        return False
    return None


def _cadastro(registro: dict[str, Any]) -> dict[str, Any]:
    cache = registro.get("cadastro")
    if cache is None:
        return {"status": "NAO_DISPONIVEL"}
    dados = cache["dados_normalizados"]
    cnae = _primeiro(dados, "opencnpj_cnae_principal")
    descricao_cnae = dados.get("cnae_principal_descricao")
    if isinstance(cnae, dict):
        descricao_cnae = descricao_cnae or cnae.get("descricao")
    return {
        "status": "DISPONIVEL",
        "razao_social": _primeiro(dados, "opencnpj_razao_social"),
        "nome_fantasia": _primeiro(dados, "opencnpj_nome_fantasia"),
        "situacao_cadastral": _primeiro(dados, "opencnpj_situacao_cadastral"),
        "matriz_filial": _primeiro(dados, "opencnpj_matriz_filial"),
        "data_inicio_atividade": _primeiro(dados, "opencnpj_data_inicio_atividade"),
        "capital_social_centavos": _capital_centavos(
            _primeiro(dados, "opencnpj_capital_social")
        ),
        "natureza_juridica": _primeiro(dados, "opencnpj_natureza_juridica"),
        "simples_nacional": _booleano_cadastral(
            _primeiro(dados, "optante_simples_nacional", "opencnpj_opcao_simples")
        ),
        "mei": _booleano_cadastral(
            _primeiro(dados, "optante_mei", "opencnpj_opcao_mei")
        ),
        "municipio_sede": dados.get("municipio_sede"),
        "uf_sede": dados.get("uf_sede"),
        "cnae_principal": {
            "codigo": dados.get("cnae_principal_codigo"),
            "descricao": descricao_cnae,
        },
        "observado_em": cache["observado_em"],
        "desatualizado": cache["cache_desatualizado"],
        "ultima_tentativa_status": cache["ultima_tentativa_status"],
    }


def obter_fornecedor(
    *,
    tipo_documento: str,
    documento: str,
    start_date: str,
    end_date: str,
    uf: str,
    municipality_tce_code: str | None,
    company_sizes: list[str],
    supplier_origins: list[str],
    expense_element_codes: list[str],
) -> dict[str, Any]:
    tipo_interno, documento_normalizado = _documento(tipo_documento, documento)
    inicio, fim, codigo_tce, portes, elementos = _filtros(
        start_date=start_date,
        end_date=end_date,
        uf=uf,
        municipality_tce_code=municipality_tce_code,
        company_sizes=company_sizes,
        expense_element_codes=expense_element_codes,
    )
    registro = tce_fornecedores.obter_resumo_fornecedor_publicado(
        tipo_documento=tipo_interno,
        documento=documento_normalizado,
        data_inicial=inicio,
        data_final=fim,
        codigo_municipio_tce=codigo_tce,
        portes=portes,
        origens=supplier_origins,
        elementos=elementos,
    )
    if registro is None:
        raise empenhos.EmpenhoRequestError(
            "SUPPLIER_NOT_FOUND",
            "Fornecedor nao encontrado no recorte publicado.",
            [{"field": "documento", "reason": "not_found", "value": documento}],
            status_code=404,
        )
    bruto = int(registro["valor_empenhado_centavos"])
    anulado = int(registro["valor_anulado_centavos"])
    liquido = int(registro["valor_liquido_centavos"])
    quantidade = int(registro["quantidade_empenhos"])
    total_recorte = int(registro["valor_total_recorte_centavos"])
    elementos_por_codigo = {
        str(item["codigo_elemento_despesa"]): item
        for item in registro["por_elemento_despesa"]
    }
    por_elemento = []
    for codigo in sorted(elementos, key=int):
        item = elementos_por_codigo.get(codigo, {})
        valor = int(item.get("valor_liquido_centavos", 0))
        por_elemento.append({
            "codigo": codigo,
            "nome": NATUREZAS_DESPESA_MONITORADAS[codigo],
            "valor_empenhado_centavos": int(item.get("valor_empenhado_centavos", 0)),
            "valor_anulado_centavos": int(item.get("valor_anulado_centavos", 0)),
            "valor_considerado_centavos": valor,
            "quantidade_empenhos": int(item.get("quantidade_empenhos", 0)),
            "percentual": (valor * 100 / liquido) if liquido else 0.0,
        })
    path = f"/pipeline/tce/fornecedores/{tipo_documento}/{documento_normalizado}/empenhos"
    return {
        "source": "TCE-CE",
        "data_source": "LOCAL_DATABASE",
        "external_requests": False,
        "filters": {
            "uf": uf.upper(),
            "municipality_tce_code": municipality_tce_code,
            "start_date": start_date,
            "end_date": end_date,
            "company_sizes": company_sizes,
            "supplier_origins": supplier_origins,
            "expense_element_codes": expense_element_codes,
        },
        "fornecedor": {
            "tipo_documento": tipo_documento,
            "documento": documento_normalizado,
            "cnpj": registro["cnpj_fornecedor"],
            "nome": registro["nome_fornecedor"],
            "porte": registro["porte_fornecedor"],
            "origens_no_recorte": registro["origens_no_recorte"],
            "cadastro": _cadastro(registro),
        },
        "recorte": {
            "start_date": start_date,
            "end_date": end_date,
            "municipality_tce_code": municipality_tce_code,
            "valor_empenhado_centavos": bruto,
            "valor_anulado_centavos": anulado,
            "valor_considerado_centavos": liquido,
            "quantidade_empenhos": quantidade,
            "quantidade_anulacoes": registro["quantidade_anulacoes"],
            "quantidade_contratos": registro["quantidade_contratos"],
            "quantidade_licitacoes": registro["quantidade_licitacoes"],
            "quantidade_municipios": registro["quantidade_municipios"],
            "valor_medio_por_empenho_centavos": liquido // quantidade if quantidade else 0,
            "percentual_anulado": (anulado * 100 / bruto) if bruto else 0.0,
            "primeiro_empenho": registro["primeiro_empenho"],
            "ultimo_empenho": registro["ultimo_empenho"],
            "meses_com_movimento": registro["meses_com_movimento"],
        },
        "evolucao_mensal": [
            {
                "competencia": item["competencia"],
                "valor_empenhado_centavos": item["valor_empenhado_centavos"],
                "valor_anulado_centavos": item["valor_anulado_centavos"],
                "valor_considerado_centavos": item["valor_liquido_centavos"],
                "quantidade_empenhos": item["quantidade_empenhos"],
            }
            for item in registro["evolucao_mensal"]
        ],
        "por_elemento_despesa": por_elemento,
        "participacao_no_recorte": {
            "valor_total_recorte_centavos": total_recorte,
            "valor_fornecedor_centavos": liquido,
            "percentual": (liquido * 100 / total_recorte) if total_recorte else 0.0,
        },
        "empenhos": {"total_items": quantidade, "path": path},
    }


def listar_empenhos_fornecedor(
    *,
    tipo_documento: str,
    documento: str,
    start_date: str,
    end_date: str,
    uf: str,
    municipality_tce_code: str | None,
    company_sizes: list[str],
    supplier_origins: list[str],
    expense_element_codes: list[str],
    search: str | None,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
) -> dict[str, Any]:
    tipo_interno, documento_normalizado = _documento(tipo_documento, documento)
    inicio, fim, codigo_tce, portes, elementos = _filtros(
        start_date=start_date,
        end_date=end_date,
        uf=uf,
        municipality_tce_code=municipality_tce_code,
        company_sizes=company_sizes,
        expense_element_codes=expense_element_codes,
    )
    resultado = tce_despesas.listar_empenhos_publicados_paginados(
        data_inicial=inicio,
        data_final=fim,
        codigo_municipio_tce=codigo_tce,
        portes=portes,
        origens=supplier_origins,
        elementos=elementos,
        busca=empenhos._normalizar_busca(search),
        pagina=page,
        tamanho_pagina=page_size,
        ordenar_por=sort_by,
        ordem=sort_order,
        tipo_documento_fornecedor=tipo_interno,
        documento_fornecedor=documento_normalizado,
    )
    total = int(resultado["total"])
    if total == 0 and page == 1 and not search:
        raise empenhos.EmpenhoRequestError(
            "SUPPLIER_NOT_FOUND",
            "Fornecedor nao encontrado no recorte publicado.",
            [{"field": "documento", "reason": "not_found", "value": documento}],
            status_code=404,
        )
    primeiro = resultado["registros"][0] if resultado["registros"] else None
    return {
        "source": "TCE-CE",
        "data_source": "LOCAL_DATABASE",
        "external_requests": False,
        "filters": {
            "uf": uf.upper(),
            "municipality_tce_code": municipality_tce_code,
            "start_date": start_date,
            "end_date": end_date,
            "company_sizes": company_sizes,
            "supplier_origins": supplier_origins,
            "expense_element_codes": expense_element_codes,
            "search": search,
        },
        "fornecedor": {
            "tipo_documento": tipo_documento,
            "documento": documento_normalizado,
            "nome": primeiro["nome_fornecedor"] if primeiro else None,
        },
        "pagination": _paginacao(total, page, page_size),
        "sorting": {"sort_by": sort_by, "sort_order": sort_order},
        "items": [empenhos._item(item) for item in resultado["registros"]],
    }


__all__ = [
    "listar_empenhos_fornecedor",
    "listar_fornecedores",
    "obter_fornecedor",
]

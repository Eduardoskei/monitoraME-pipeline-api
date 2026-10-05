from __future__ import annotations

from datetime import date
from math import ceil
import re
from typing import Any

from app.core import database
from app.core.config import NATUREZAS_DESPESA_MONITORADAS, UF_PADRAO
from app.pipeline.persistence import tce_despesas as persistence


PORTES_FILTRO = {
    "ME": "ME",
    "MEI": "MEI",
    "EPP": "EPP",
    "OTHER": "DEMAIS",
    "UNKNOWN": "NAO_IDENTIFICADO",
}


class EmpenhoRequestError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        details: list[dict[str, Any]],
        *,
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status_code = status_code


def _data_iso(valor: str, campo: str) -> date:
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError) as error:
        raise EmpenhoRequestError(
            "INVALID_DATE",
            f"{campo} deve usar o formato YYYY-MM-DD.",
            [{"field": campo, "reason": "invalid_date", "value": valor}],
        ) from error


def _municipio(codigo_ibge: str | None, uf: str) -> dict[str, str] | None:
    if codigo_ibge is None:
        return None
    municipio = database.localizar_municipio_tce_por_ibge(codigo_ibge, uf=uf)
    if municipio is None:
        raise EmpenhoRequestError(
            "MUNICIPALITY_NOT_FOUND",
            "Municipio nao encontrado no catalogo local.",
            [{
                "field": "municipality_ibge_code",
                "reason": "not_found",
                "value": codigo_ibge,
            }],
            status_code=404,
        )
    return municipio


def _display_id(registro: dict[str, Any]) -> str:
    exercicio = int(registro["exercicio_orcamento"]) // 100
    return f"EMP-{exercicio}-{registro['numero_empenho']}"


def _normalizar_busca(valor: str | None) -> str | None:
    if valor is None:
        return None
    busca = valor.strip()
    identificador = re.fullmatch(r"EMP-\d{4}-(.+)", busca, flags=re.IGNORECASE)
    return identificador.group(1) if identificador else busca


def _item(registro: dict[str, Any]) -> dict[str, Any]:
    elemento = str(registro["codigo_elemento_despesa"])
    return {
        "chave_empenho": registro["chave_empenho"],
        "display_id": _display_id(registro),
        "numero_empenho": registro["numero_empenho"],
        "data_empenho": registro["data_empenho"],
        "municipio_comprador": {
            "codigo_ibge": registro["codigo_municipio_ibge"],
            "codigo_tce": registro["codigo_municipio_tce"],
            "nome": registro["municipio_comprador"],
            "uf": registro["uf_comprador"],
        },
        "fornecedor": {
            "documento": registro["documento_fornecedor"],
            "cnpj": registro["cnpj_fornecedor"],
            "nome": registro["nome_fornecedor"],
            "porte": registro["porte_fornecedor"],
            "origem": registro["origem_fornecedor"],
        },
        "elemento_despesa": {
            "codigo_natureza": registro["codigo_natureza_despesa"],
            "codigo_exibicao": str(registro["codigo_natureza_despesa"])[:6],
            "codigo_elemento": elemento,
            "nome": NATUREZAS_DESPESA_MONITORADAS[elemento],
        },
        "valores": {
            "valor_empenhado_centavos": int(registro["valor_empenhado_centavos"]),
            "valor_anulado_centavos": int(registro["valor_anulado_centavos"]),
            "valor_liquido_centavos": int(registro["valor_liquido_centavos"]),
        },
        "possui_anulacao": int(registro["quantidade_anulacoes"]) > 0,
        "quantidade_anulacoes": int(registro["quantidade_anulacoes"]),
    }


def listar_empenhos(
    *,
    start_date: str,
    end_date: str,
    uf: str,
    municipality_ibge_code: str | None,
    company_sizes: list[str],
    supplier_origins: list[str],
    expense_element_codes: list[str],
    search: str | None,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
) -> dict[str, Any]:
    inicio, fim = _data_iso(start_date, "start_date"), _data_iso(end_date, "end_date")
    if inicio > fim:
        raise EmpenhoRequestError(
            "INVALID_DATE_RANGE",
            "start_date deve ser menor ou igual a end_date.",
            [{"field": "start_date", "reason": "after_end_date", "value": start_date}],
        )
    uf_normalizada = uf.strip().upper()
    if uf_normalizada != UF_PADRAO.upper():
        raise EmpenhoRequestError(
            "UNSUPPORTED_UF",
            f"A consulta possui dados apenas para a UF {UF_PADRAO.upper()}.",
            [{"field": "uf", "reason": "unsupported_uf", "value": uf}],
        )
    invalidos = sorted(set(expense_element_codes) - set(NATUREZAS_DESPESA_MONITORADAS))
    if invalidos:
        raise EmpenhoRequestError(
            "INVALID_EXPENSE_ELEMENT_CODE",
            "expense_element_codes possui codigos nao monitorados.",
            [{"field": "expense_element_codes", "reason": "unsupported_value", "value": invalidos}],
        )
    municipio = _municipio(municipality_ibge_code, uf_normalizada)
    portes = [PORTES_FILTRO[item] for item in company_sizes]
    resultado = persistence.listar_empenhos_publicados_paginados(
        data_inicial=inicio,
        data_final=fim,
        codigo_municipio_tce=municipio["codigo_municipio_tce"] if municipio else None,
        portes=portes,
        origens=supplier_origins,
        elementos=expense_element_codes or list(NATUREZAS_DESPESA_MONITORADAS),
        busca=_normalizar_busca(search),
        pagina=page,
        tamanho_pagina=page_size,
        ordenar_por=sort_by,
        ordem=sort_order,
    )
    total = int(resultado["total"])
    paginas = ceil(total / page_size) if total else 0
    return {
        "source": "TCE-CE",
        "data_source": "LOCAL_DATABASE",
        "external_requests": False,
        "filters": {
            "uf": uf_normalizada,
            "municipality_ibge_code": municipality_ibge_code,
            "start_date": start_date,
            "end_date": end_date,
            "company_sizes": company_sizes,
            "supplier_origins": supplier_origins,
            "expense_element_codes": expense_element_codes,
            "search": search,
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total,
            "total_pages": paginas,
            "has_previous": page > 1,
            "has_next": page < paginas,
        },
        "sorting": {"sort_by": sort_by, "sort_order": sort_order},
        "items": [_item(item) for item in resultado["registros"]],
    }


def obter_empenho(chave_empenho: str) -> dict[str, Any]:
    registro = persistence.obter_empenho_publicado_por_chave(chave_empenho)
    if registro is None:
        raise EmpenhoRequestError(
            "COMMITMENT_NOT_FOUND",
            "Empenho nao encontrado entre os dados publicados.",
            [{"field": "chave_empenho", "reason": "not_found", "value": chave_empenho}],
            status_code=404,
        )
    basico = _item(registro)
    anulacoes = persistence.listar_anulacoes_publicadas_por_empenho(
        chave_empenho=chave_empenho,
        codigo_municipio_tce=registro["codigo_municipio_tce"],
    )
    return {
        "source": "TCE-CE",
        "data_source": "LOCAL_DATABASE",
        "external_requests": False,
        **basico,
        "competencia": registro["competencia"],
        "classificacao_orcamentaria": {
            "exercicio": int(registro["exercicio_orcamento"]) // 100,
            "exercicio_orcamento_tce": registro["exercicio_orcamento"],
            "codigo_orgao": registro["codigo_orgao"],
            "codigo_unidade_orcamentaria": registro["codigo_unidade_orcamentaria"],
            **basico["elemento_despesa"],
        },
        "fornecedor": {
            **basico["fornecedor"],
            "tipo_documento": registro["tipo_documento_fornecedor"],
            "cpf": registro["cpf_fornecedor"],
            "municipio_sede": registro["municipio_fornecedor"],
            "uf_sede": registro["uf_fornecedor"],
            "cnae_principal_codigo": registro["cnae_principal_codigo"],
            "cnae_principal_descricao": registro["cnae_principal_descricao"],
            "observado_em": registro["fornecedor_observado_em"],
        },
        "referencias": {
            "estado_empenho": registro["estado_empenho"],
            "numero_contrato": registro["numero_contrato"],
            "numero_licitacao": registro["numero_licitacao"],
            "numero_empenho_substituto": registro["numero_empenho_substituto"],
            "numero_nota_anulacao_informado": registro["numero_nota_anulacao_informado"],
        },
        "anulacoes": anulacoes,
        "publicacao": {
            "run_id": registro["run_id"],
            "status": registro["status_publicacao"],
            "publicado_em": registro["publicado_em"],
        },
    }


__all__ = ["EmpenhoRequestError", "listar_empenhos", "obter_empenho"]

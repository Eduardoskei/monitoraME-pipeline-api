from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


COMPARISONS_PATH = "/pipeline/tce/comparisons"
EMPENHOS_PATH = "/pipeline/tce/empenhos"
REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def obter_request_id(request: Request) -> str:
    recebido = request.headers.get(REQUEST_ID_HEADER, "").strip()
    if recebido and _REQUEST_ID_PATTERN.fullmatch(recebido):
        return recebido
    return f"req_{uuid4().hex}"


def resposta_erro(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]],
    request_id: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers={REQUEST_ID_HEADER: request_id},
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": request_id,
            }
        },
    )


def usa_erro_padronizado(path: str) -> bool:
    return path == COMPARISONS_PATH or path == EMPENHOS_PATH or path.startswith(
        f"{EMPENHOS_PATH}/"
    )


def resposta_erro_validacao(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    request_id = obter_request_id(request)
    details: list[dict[str, Any]] = []
    for item in error.errors():
        localizacao = [
            str(parte)
            for parte in item.get("loc", ())
            if parte not in {"body", "query", "path"}
        ]
        detalhe: dict[str, Any] = {
            "field": ".".join(localizacao) or "body",
            "reason": str(item.get("type", "invalid_value")),
        }
        if "input" in item:
            detalhe["value"] = item["input"]
        details.append(detalhe)

    code = "INVALID_REQUEST"
    message = "A requisicao possui campos invalidos."
    if request.url.path == EMPENHOS_PATH and details:
        campo = details[0]["field"]
        code, message = {
            "page": ("INVALID_PAGE", "page deve ser maior ou igual a 1."),
            "page_size": (
                "INVALID_PAGE_SIZE",
                "page_size deve estar entre 1 e 100.",
            ),
            "sort_by": ("INVALID_SORT_FIELD", "sort_by possui um valor invalido."),
            "start_date": ("INVALID_DATE", "start_date deve usar o formato YYYY-MM-DD."),
            "end_date": ("INVALID_DATE", "end_date deve usar o formato YYYY-MM-DD."),
        }.get(campo, ("INVALID_FILTER", "Um ou mais filtros possuem valores invalidos."))

    return resposta_erro(
        status_code=422,
        code=code,
        message=message,
        details=details,
        request_id=request_id,
    )


resposta_erro_validacao_comparison = resposta_erro_validacao


__all__ = [
    "COMPARISONS_PATH",
    "EMPENHOS_PATH",
    "REQUEST_ID_HEADER",
    "obter_request_id",
    "resposta_erro",
    "resposta_erro_validacao",
    "resposta_erro_validacao_comparison",
    "usa_erro_padronizado",
]

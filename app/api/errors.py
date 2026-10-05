from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


COMPARISONS_PATH = "/pipeline/tce/comparisons"
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


def resposta_erro_validacao_comparison(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    request_id = obter_request_id(request)
    details: list[dict[str, Any]] = []
    for item in error.errors():
        localizacao = [str(parte) for parte in item.get("loc", ()) if parte != "body"]
        detalhe: dict[str, Any] = {
            "field": ".".join(localizacao) or "body",
            "reason": str(item.get("type", "invalid_value")),
        }
        if "input" in item:
            detalhe["value"] = item["input"]
        details.append(detalhe)

    return resposta_erro(
        status_code=422,
        code="INVALID_REQUEST",
        message="O corpo da requisicao possui campos invalidos.",
        details=details,
        request_id=request_id,
    )


__all__ = [
    "COMPARISONS_PATH",
    "REQUEST_ID_HEADER",
    "obter_request_id",
    "resposta_erro",
    "resposta_erro_validacao_comparison",
]

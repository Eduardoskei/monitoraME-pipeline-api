"""Visao Geral publica, conforme contrato 0.9.1."""
from collections import OrderedDict
from threading import Lock
from time import monotonic
from uuid import uuid4
from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from app.analytics.models import PublicFilters, PublicOverviewResponse
from app.analytics.repository import load_snapshot
from app.analytics.service import AnalyticsError, build_overview

class Utf8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


def error_response(request, code, message, status, details=None):
    return Utf8JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "details": details or [],
        "request_id": request.state.analytics_request_id,
    }})

# Per-worker protection. At deployment the ingress must enforce a global limit.
_clients = OrderedDict()
_lock = Lock()


def rate_allowed(client):
    now = monotonic()
    with _lock:
        while _clients and next(iter(_clients.values()))[0] <= now - 60:
            _clients.popitem(last=False)
        if client not in _clients:
            if len(_clients) >= 10000:
                return False
            _clients[client] = [now, 0]
        entry = _clients[client]
        entry[1] += 1
        return entry[1] <= 60


class PublicRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request):
            request.state.analytics_request_id = "req_" + uuid4().hex
            client = request.client.host if request.client else "unknown"
            if not rate_allowed(client):
                response = error_response(request, "RATE_LIMIT_EXCEEDED", "Limite de requisicoes excedido.", 429)
                response.headers["Retry-After"] = "60"
                return response
            try:
                forbidden = {"company_size", "company_sizes"}.intersection(request.query_params)
                if forbidden:
                    raise AnalyticsError("COMPANY_SIZE_NOT_ALLOWED", "Escopo publico fixo em ME; filtros de porte sao proibidos.", 422, sorted(forbidden)[0])
                return await handler(request)
            except AnalyticsError as error:
                return error_response(request, error.code, error.message, error.status,
                                      [{"field": error.field, "reason": error.code, "value": None}])
            except (ValidationError, RequestValidationError) as error:
                details = [{"field": str(e["loc"][-1]) if e["loc"] else None,
                            "reason": e["type"], "value": None} for e in error.errors()]
                return error_response(request, "INVALID_FILTER", "Filtros invalidos; datas devem usar YYYY-MM-DD.", 422, details)
        return wrapped


router = APIRouter(prefix="/api/v1/analysis/public", tags=["Analise publica"], route_class=PublicRoute,
                   default_response_class=Utf8JSONResponse)


@router.get("/overview", response_model=PublicOverviewResponse,
            summary="Visao Geral publica exclusiva de ME",
            description="Empenhado liquido de ME. Participacao em todas as compras permanece null ate aprovacao de Produto/Juridico.",
            responses={422: {"description": "Filtro invalido ou filtro de porte proibido."},
                       429: {"description": "Limite de requisicoes excedido."},
                       503: {"description": "Nenhuma carga valida de empenhos."}})
def public_overview(request: Request, start_date: str, end_date: str, uf: str = "CE",
                    municipality_ibge_code: str | None = None,
                    expense_element_codes: list[str] = Query(default=[]),
                    supplier_origins: list[str] = Query(default=[])) -> PublicOverviewResponse:
    allowed = {"start_date", "end_date", "uf", "municipality_ibge_code", "expense_element_codes", "supplier_origins"}
    if set(request.query_params) - allowed:
        raise AnalyticsError("INVALID_FILTER", "Parametro nao permitido no contrato publico.", 422)
    for key in allowed - {"expense_element_codes", "supplier_origins"}:
        if len(request.query_params.getlist(key)) > 1:
            raise AnalyticsError("INVALID_FILTER", "Parametro escalar repetido.", 422, key)
    filters = PublicFilters(start_date=start_date, end_date=end_date, uf=uf,
                            municipality_ibge_code=municipality_ibge_code,
                            expense_element_codes=[] if expense_element_codes == ["[]"] else expense_element_codes,
                            supplier_origins=[] if supplier_origins == ["[]"] else supplier_origins)
    return build_overview(load_snapshot(), filters, request.state.analytics_request_id)

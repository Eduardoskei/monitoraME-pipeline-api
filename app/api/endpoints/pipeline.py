import logging
from typing import Annotated, Any
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from app.api.errors import REQUEST_ID_HEADER, obter_request_id, resposta_erro
from app.api.schemas.comparisons import CompanySize, SupplierOrigin, TceComparisonRequest
from app.api.schemas.empenhos import EmpenhoSortField, SortOrder
from app.api.schemas.fornecedores import SupplierDocumentType, SupplierSortField
from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, UF_PADRAO
from app.pipeline import analisys, comparisons, empenhos, fornecedores, territorial
from app.pipeline.ingestion.fornecedores import FonteCadastralIndisponivelError
from app.pipeline.ingestion import tce
from app.pipeline.kpis import DadosInsuficientesKPI
from app.utils import DATA_ISO_FORMATO, normalizar_data_iso


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/pipeline",
    tags=["Pipeline"],
    responses={
        422: {
            "description": "Parametros invalidos ou dados insuficientes para o processamento.",
        },
        503: {"description": "Fonte externa indisponivel no momento da consulta."},
    },
)

DATA_ISO_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


def _data_inicial_query():
    return Query(description=f"Data inicial em {DATA_ISO_FORMATO}.", pattern=DATA_ISO_PATTERN)


def _data_final_query():
    return Query(description=f"Data final em {DATA_ISO_FORMATO}.", pattern=DATA_ISO_PATTERN)


def _validar_periodo_api(data_inicial: str, data_final: str) -> None:
    normalizar_data_iso(data_inicial, "%Y-%m-%d")
    normalizar_data_iso(data_final, "%Y-%m-%d")


def _erro_pipeline(error: Exception) -> HTTPException:
    if isinstance(error, (ValueError, TypeError, DadosInsuficientesKPI)):
        logger.info(
            "Pipeline rejeitou os parâmetros ou os dados disponíveis",
            extra={
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(
        error,
        (
            FonteCadastralIndisponivelError,
            analisys.CompetenciasTceIndisponiveisError,
            tce.TceIndisponivelError,
        ),
    ):
        logger.warning(
            "Pipeline indisponível por falha em dependência",
            extra={
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        return HTTPException(status_code=503, detail=str(error))
    logger.exception(
        "Falha inesperada ao executar o pipeline",
        extra={
            "error_type": type(error).__name__,
        },
    )
    return HTTPException(status_code=500, detail="Falha inesperada ao executar o pipeline.")


@router.get(
    "/tce/contratos",
    summary="Consulta contratos no TCE-CE",
    description="Executa o fluxo de ingestao, limpeza e enriquecimento dos contratos publicados pelo TCE-CE.",
    response_description="Contratos processados, totais e metadados da consulta TCE-CE.",
)
def tce_contratos(
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
    codigo_municipio: Annotated[
        str,
        Query(description="Codigo do municipio no TCE-CE."),
    ] = CODIGO_MUNICIPIO_TCE_PADRAO,
    enriquecer_fornecedores: Annotated[
        bool,
        Query(description="Enriquece os dados com informacoes cadastrais dos fornecedores."),
    ] = False,
    limite: Annotated[
        int | None,
        Query(description="Limite de contratos retornados.", ge=1, le=1000),
    ] = 100,
) -> dict[str, object]:
    try:
        _validar_periodo_api(data_inicial, data_final)
        return analisys.consultar_tce_contratos(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=codigo_municipio,
            enriquecer_fornecedores=enriquecer_fornecedores,
            limite=limite,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error


@router.get(
    "/tce/kpis/portes-por-mes",
    summary="Calcula participação por porte empresarial por mês",
    description=(
        "Atualiza as competências solicitadas e calcula quantidade, valor líquido "
        "empenhado e percentual mensal de MEI, ME, EPP, demais e não identificados."
    ),
    response_description=(
        "Série mensal por porte, totais e metadados do TCE-CE."
    ),
)
def tce_kpi_portes_por_mes(
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
    codigo_municipio: Annotated[
        str,
        Query(description="Código do município no TCE-CE."),
    ] = CODIGO_MUNICIPIO_TCE_PADRAO,
    limite: Annotated[
        int | None,
        Query(
            description="Limite de registros retornados.",
            ge=1,
            le=1000,
        ),
    ] = 100,
) -> dict[str, object]:
    try:
        _validar_periodo_api(data_inicial, data_final)
        return analisys.consultar_kpi_tce_portes_por_mes(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=codigo_municipio,
            limite=limite,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error


@router.get(
    "/tce/overview",
    summary="Consulta o overview de ME e MEI",
    description=(
        "Sem município, consolida os lotes publicados de todo o Ceará usando somente "
        "o banco local. Com município, atualiza as competências solicitadas. Calcula "
        "a participação de ME + MEI e restringe o detalhamento geográfico a ME e MEI."
    ),
    response_description="KPIs, evolução mensal e destino dos recursos de ME e MEI.",
)
def tce_overview(
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
    codigo_municipio: Annotated[
        str | None,
        Query(
            description=(
                "Código do município no TCE-CE. Quando omitido, consolida "
                "todos os municípios do Ceará com lotes publicados."
            )
        ),
    ] = None,
) -> dict[str, object]:
    try:
        _validar_periodo_api(data_inicial, data_final)
        return analisys.consultar_overview_tce(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=codigo_municipio,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error


@router.get(
    "/tce/overview/municipal",
    summary="Consulta o overview de um municipio por periodo",
    description=(
        "Busca o overview municipal de ME e MEI para o periodo informado, "
        "usando as mesmas regras e indicadores do overview."
    ),
    response_description="Overview do municipio e periodo informados.",
)
def tce_overview_municipal(
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
    codigo_municipio_tce: Annotated[
        str,
        Query(description="Codigo do municipio no TCE-CE.", pattern=r"^\d{3}$"),
    ],
) -> dict[str, object]:
    try:
        _validar_periodo_api(data_inicial, data_final)
        resultado = analisys.consultar_overview_tce(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=codigo_municipio_tce,
        )
        return {
            **resultado,
            "parametros": {
                **dict(resultado.get("parametros") or {}),
                "data_inicial": data_inicial,
                "data_final": data_final,
                "codigo_municipio": codigo_municipio_tce,
            },
        }
    except Exception as error:
        raise _erro_pipeline(error) from error


@router.post(
    "/tce/comparisons",
    summary="Compara dois overviews armazenados do TCE-CE",
    description=(
        "Compara municipios, periodos ou ambos usando exclusivamente competencias "
        "e fornecedores ja armazenados no banco. Nao executa ingestao nem consulta fontes externas."
    ),
    response_description="Overviews completos dos dois lados e secoes com maior, menor e diferenca.",
    response_model=None,
    responses={
        404: {"description": "Municipio nao encontrado no catalogo local."},
        422: {"description": "Filtros invalidos ou dados locais indisponiveis."},
        500: {"description": "Falha inesperada ao consultar o banco local."},
    },
)
def tce_comparisons(
    payload: TceComparisonRequest,
    request: Request,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return comparisons.consultar_comparacao_tce(payload.model_dump(mode="json"))
    except comparisons.ComparisonRequestError as error:
        logger.info(
            "Comparacao rejeitada pelos filtros ou dados locais",
            extra={
                "request_id": request_id,
                "error_code": error.code,
                "error": error.message,
            },
        )
        return resposta_erro(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            details=error.details,
            request_id=request_id,
        )
    except Exception as error:
        logger.exception(
            "Falha inesperada ao consultar comparacao",
            extra={
                "request_id": request_id,
                "error_type": type(error).__name__,
            },
        )
        return resposta_erro(
            status_code=500,
            code="INTERNAL_ERROR",
            message="Falha inesperada ao consultar os dados da comparacao.",
            details=[],
            request_id=request_id,
        )


def _erro_empenhos(
    error: Exception,
    *,
    request_id: str,
    mensagem_interna: str,
) -> JSONResponse:
    if isinstance(error, empenhos.EmpenhoRequestError):
        logger.info(
            "Consulta local rejeitada",
            extra={"request_id": request_id, "error_code": error.code, "error": error.message},
        )
        return resposta_erro(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            details=error.details,
            request_id=request_id,
        )
    logger.exception(
        "Falha inesperada ao consultar dados locais",
        extra={"request_id": request_id, "error_type": type(error).__name__},
    )
    return resposta_erro(
        status_code=500,
        code="INTERNAL_ERROR",
        message=mensagem_interna,
        details=[],
        request_id=request_id,
    )


@router.get(
    "/tce/empenhos",
    summary="Lista empenhos publicados armazenados",
    description=(
        "Lista e pagina empenhos dos sete elementos monitorados usando somente "
        "lotes publicados e fornecedores armazenados no banco local."
    ),
    response_model=None,
)
def tce_empenhos(
    request: Request,
    response: Response,
    start_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    end_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    uf: Annotated[str, Query(min_length=2, max_length=2)] = UF_PADRAO,
    municipality_tce_code: Annotated[str | None, Query(pattern=r"^\d{3}$")] = None,
    company_sizes: Annotated[list[CompanySize] | None, Query()] = None,
    supplier_origins: Annotated[list[SupplierOrigin] | None, Query()] = None,
    expense_element_codes: Annotated[list[str] | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    sort_by: EmpenhoSortField = EmpenhoSortField.NET_VALUE,
    sort_order: SortOrder = SortOrder.DESC,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return empenhos.listar_empenhos(
            start_date=start_date,
            end_date=end_date,
            uf=uf,
            municipality_tce_code=municipality_tce_code,
            company_sizes=[item.value for item in company_sizes or []],
            supplier_origins=[item.value for item in supplier_origins or []],
            expense_element_codes=expense_element_codes or [],
            search=search,
            page=page,
            page_size=page_size,
            sort_by=sort_by.value,
            sort_order=sort_order.value,
        )
    except Exception as error:
        return _erro_empenhos(
            error,
            request_id=request_id,
            mensagem_interna="Falha inesperada ao listar os empenhos.",
        )


@router.get(
    "/tce/empenhos/{chave_empenho}",
    summary="Detalha um empenho publicado pela chave canonica",
    description=(
        "Retorna dados do empenho, valor bruto, anulacoes e valor liquido sem "
        "consultar fontes externas."
    ),
    response_model=None,
)
def tce_empenho_detalhe(
    chave_empenho: str,
    request: Request,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return empenhos.obter_empenho(chave_empenho)
    except Exception as error:
        return _erro_empenhos(
            error,
            request_id=request_id,
            mensagem_interna="Falha inesperada ao consultar o empenho.",
        )


@router.get(
    "/tce/fornecedores",
    summary="Lista fornecedores presentes nos empenhos publicados",
    description=(
        "Agrupa fornecedores e valores do recorte usando exclusivamente dados "
        "publicados e cadastros armazenados no banco local."
    ),
    response_model=None,
)
def tce_fornecedores(
    request: Request,
    response: Response,
    start_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    end_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    uf: Annotated[str, Query(min_length=2, max_length=2)] = UF_PADRAO,
    municipality_tce_code: Annotated[str | None, Query(pattern=r"^\d{3}$")] = None,
    company_sizes: Annotated[list[CompanySize] | None, Query()] = None,
    supplier_origins: Annotated[list[SupplierOrigin] | None, Query()] = None,
    expense_element_codes: Annotated[list[str] | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    sort_by: SupplierSortField = SupplierSortField.NET_VALUE,
    sort_order: SortOrder = SortOrder.DESC,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return fornecedores.listar_fornecedores(
            start_date=start_date,
            end_date=end_date,
            uf=uf,
            municipality_tce_code=municipality_tce_code,
            company_sizes=[item.value for item in company_sizes or []],
            supplier_origins=[item.value for item in supplier_origins or []],
            expense_element_codes=expense_element_codes or [],
            search=search,
            page=page,
            page_size=page_size,
            sort_by=sort_by.value,
            sort_order=sort_order.value,
        )
    except Exception as error:
        return _erro_empenhos(
            error,
            request_id=request_id,
            mensagem_interna="Falha inesperada ao listar os fornecedores.",
        )


@router.get(
    "/tce/fornecedores/{tipo_documento}/{documento}",
    summary="Consulta o resumo analitico de um fornecedor",
    description=(
        "Retorna cadastro armazenado, indicadores, evolucao mensal, elementos "
        "de despesa e participacao do fornecedor no recorte."
    ),
    response_model=None,
)
def tce_fornecedor_detalhe(
    tipo_documento: SupplierDocumentType,
    documento: str,
    request: Request,
    response: Response,
    start_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    end_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    uf: Annotated[str, Query(min_length=2, max_length=2)] = UF_PADRAO,
    municipality_tce_code: Annotated[str | None, Query(pattern=r"^\d{3}$")] = None,
    company_sizes: Annotated[list[CompanySize] | None, Query()] = None,
    supplier_origins: Annotated[list[SupplierOrigin] | None, Query()] = None,
    expense_element_codes: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return fornecedores.obter_fornecedor(
            tipo_documento=tipo_documento.value,
            documento=documento,
            start_date=start_date,
            end_date=end_date,
            uf=uf,
            municipality_tce_code=municipality_tce_code,
            company_sizes=[item.value for item in company_sizes or []],
            supplier_origins=[item.value for item in supplier_origins or []],
            expense_element_codes=expense_element_codes or [],
        )
    except Exception as error:
        return _erro_empenhos(
            error,
            request_id=request_id,
            mensagem_interna="Falha inesperada ao consultar o fornecedor.",
        )


@router.get(
    "/tce/fornecedores/{tipo_documento}/{documento}/empenhos",
    summary="Lista os empenhos publicados de um fornecedor",
    description=(
        "Pagina as notas consideradas para o fornecedor e recorte sem recalcular "
        "os graficos da rota de detalhe."
    ),
    response_model=None,
)
def tce_fornecedor_empenhos(
    tipo_documento: SupplierDocumentType,
    documento: str,
    request: Request,
    response: Response,
    start_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    end_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    uf: Annotated[str, Query(min_length=2, max_length=2)] = UF_PADRAO,
    municipality_tce_code: Annotated[str | None, Query(pattern=r"^\d{3}$")] = None,
    company_sizes: Annotated[list[CompanySize] | None, Query()] = None,
    supplier_origins: Annotated[list[SupplierOrigin] | None, Query()] = None,
    expense_element_codes: Annotated[list[str] | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    sort_by: EmpenhoSortField = EmpenhoSortField.NET_VALUE,
    sort_order: SortOrder = SortOrder.DESC,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return fornecedores.listar_empenhos_fornecedor(
            tipo_documento=tipo_documento.value,
            documento=documento,
            start_date=start_date,
            end_date=end_date,
            uf=uf,
            municipality_tce_code=municipality_tce_code,
            company_sizes=[item.value for item in company_sizes or []],
            supplier_origins=[item.value for item in supplier_origins or []],
            expense_element_codes=expense_element_codes or [],
            search=search,
            page=page,
            page_size=page_size,
            sort_by=sort_by.value,
            sort_order=sort_order.value,
        )
    except Exception as error:
        return _erro_empenhos(
            error,
            request_id=request_id,
            mensagem_interna="Falha inesperada ao listar os empenhos do fornecedor.",
        )


@router.get(
    "/tce/analise-territorial",
    summary="Analisa a distribuicao territorial das compras municipais",
    description=(
        "Calcula retencao local, destino dos recursos, portes, elementos, evolucao, "
        "concentracao e ranking usando apenas empenhos publicados e cadastros locais."
    ),
    response_model=None,
)
def tce_analise_territorial(
    request: Request,
    response: Response,
    start_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    end_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    uf: Annotated[str, Query(min_length=2, max_length=2)] = UF_PADRAO,
    municipality_tce_code: Annotated[str, Query(pattern=r"^\d{3}$")] = CODIGO_MUNICIPIO_TCE_PADRAO,
    company_sizes: Annotated[list[CompanySize] | None, Query()] = None,
    supplier_origins: Annotated[list[SupplierOrigin] | None, Query()] = None,
    expense_element_codes: Annotated[list[str] | None, Query()] = None,
    ranking_limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return territorial.consultar_analise_territorial(
            start_date=start_date,
            end_date=end_date,
            uf=uf,
            municipality_tce_code=municipality_tce_code,
            company_sizes=[item.value for item in company_sizes or []],
            supplier_origins=[item.value for item in supplier_origins or []],
            expense_element_codes=expense_element_codes or [],
            ranking_limit=ranking_limit,
        )
    except Exception as error:
        return _erro_empenhos(
            error,
            request_id=request_id,
            mensagem_interna="Falha inesperada ao calcular a analise territorial.",
        )


@router.get(
    "/tce/analitico/indicadores",
    summary="Calcula indicadores analiticos principais do TCE-CE",
    description=(
        "Atualiza as competências solicitadas e calcula os indicadores sobre "
        "empenhos líquidos publicados do TCE-CE. "
        "O municipio comprador e resolvido automaticamente pelo codigo interno do TCE-CE."
    ),
    response_description="Indicadores analiticos, totais e metadados da consulta TCE-CE.",
)
def tce_analitico_indicadores(
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
    codigo_municipio: Annotated[
        str,
        Query(description="Codigo do municipio no TCE-CE."),
    ] = CODIGO_MUNICIPIO_TCE_PADRAO,
    limite_ranking: Annotated[
        int,
        Query(description="Quantidade maxima de fornecedores no ranking.", ge=1, le=100),
    ] = 10,
    limite: Annotated[
        int | None,
        Query(description="Limite de registros por tabela de indicador retornada.", ge=1, le=1000),
    ] = 100,
) -> dict[str, object]:
    try:
        _validar_periodo_api(data_inicial, data_final)
        return analisys.consultar_tce_indicadores_analiticos(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=codigo_municipio,
            limite_ranking=limite_ranking,
            limite=limite,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error

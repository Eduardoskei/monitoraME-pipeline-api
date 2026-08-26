from typing import Annotated
from fastapi import APIRouter, HTTPException, Query
from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, MODALIDADE_ID_PADRAO, UF_PADRAO
from app.pipeline import analisys
from app.pipeline.ingestion.fornecedores import FonteCadastralIndisponivelError
from app.pipeline.ingestion.pncp import PncpIndisponivelError
from app.pipeline.kpis import DadosInsuficientesKPI


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


def _erro_pipeline(error: Exception) -> HTTPException:
    if isinstance(error, (ValueError, TypeError, DadosInsuficientesKPI)):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, (PncpIndisponivelError, FonteCadastralIndisponivelError)):
        return HTTPException(status_code=503, detail=str(error))
    return HTTPException(status_code=500, detail="Falha inesperada ao executar o pipeline.")


@router.get(
    "/pncp/contratacoes",
    summary="Consulta contratacoes publicadas no PNCP",
    description="Executa o fluxo de ingestao, limpeza e enriquecimento das contratacoes publicadas no PNCP.",
    response_description="Tabelas processadas, totais e metadados da consulta PNCP.",
)
def pncp_contratacoes(
    data_inicial: Annotated[str, Query(description="Data inicial em YYYY-MM-DD ou YYYYMMDD.")],
    data_final: Annotated[str, Query(description="Data final em YYYY-MM-DD ou YYYYMMDD.")],
    modalidade_id: Annotated[
        int,
        Query(description="Identificador da modalidade no PNCP.", ge=1),
    ] = MODALIDADE_ID_PADRAO,
    uf: Annotated[
        str | None,
        Query(description="UF usada como filtro e enriquecimento.", min_length=2, max_length=2),
    ] = UF_PADRAO,
    codigo_municipio_ibge: Annotated[
        str | None,
        Query(description="Codigo IBGE do municipio consultado."),
    ] = None,
    cnpj_orgao: Annotated[
        str | None,
        Query(description="CNPJ do orgao responsavel pela contratacao."),
    ] = None,
    max_paginas: Annotated[
        int | None,
        Query(description="Quantidade maxima de paginas consultadas no PNCP.", ge=1),
    ] = 1,
    incluir_detalhes: Annotated[
        bool,
        Query(description="Inclui detalhes completos de compras, itens e contratos."),
    ] = False,
    enriquecer_municipios: Annotated[
        bool,
        Query(description="Enriquece os dados com municipios do IBGE."),
    ] = True,
    enriquecer_fornecedores: Annotated[
        bool,
        Query(description="Enriquece os dados com informacoes cadastrais dos fornecedores."),
    ] = False,
    limite: Annotated[
        int | None,
        Query(description="Limite de registros por tabela retornada.", ge=1, le=1000),
    ] = 100,
) -> dict[str, object]:
    try:
        return analisys.consultar_pncp_contratacoes(
            data_inicial=data_inicial,
            data_final=data_final,
            modalidade_id=modalidade_id,
            uf=uf,
            codigo_municipio_ibge=codigo_municipio_ibge,
            cnpj_orgao=cnpj_orgao,
            max_paginas=max_paginas,
            incluir_detalhes=incluir_detalhes,
            enriquecer_municipios=enriquecer_municipios,
            enriquecer_fornecedores=enriquecer_fornecedores,
            limite=limite,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error


@router.get(
    "/tce/contratos",
    summary="Consulta contratos no TCE-CE",
    description="Executa o fluxo de ingestao, limpeza e enriquecimento dos contratos publicados pelo TCE-CE.",
    response_description="Contratos processados, totais e metadados da consulta TCE-CE.",
)
def tce_contratos(
    data_inicial: Annotated[str, Query(description="Data inicial em YYYY-MM-DD ou YYYYMMDD.")],
    data_final: Annotated[str, Query(description="Data final em YYYY-MM-DD ou YYYYMMDD.")],
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
    "/tce/kpis/me-por-mes",
    summary="Calcula participacao de ME por mes",
    description="Calcula a participacao mensal de microempresas nos contratos do TCE-CE.",
    response_description="Serie mensal do KPI, totais e metadados da consulta TCE-CE.",
)
def tce_kpi_me_por_mes(
    data_inicial: Annotated[str, Query(description="Data inicial em YYYY-MM-DD ou YYYYMMDD.")],
    data_final: Annotated[str, Query(description="Data final em YYYY-MM-DD ou YYYYMMDD.")],
    codigo_municipio: Annotated[
        str,
        Query(description="Codigo do municipio no TCE-CE."),
    ] = CODIGO_MUNICIPIO_TCE_PADRAO,
    limite: Annotated[
        int | None,
        Query(description="Limite de periodos retornados.", ge=1, le=1000),
    ] = 100,
) -> dict[str, object]:
    try:
        return analisys.consultar_kpi_tce_me_por_mes(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=codigo_municipio,
            limite=limite,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error

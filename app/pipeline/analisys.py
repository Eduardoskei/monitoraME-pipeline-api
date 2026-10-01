from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
import pandas as pd
from app.core import database
from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, UF_PADRAO
from app.pipeline import analitico, kpis, merge
from app.pipeline import tce_despesas as tce_despesas_pipeline
from app.pipeline.cleaners import tce as tce_cleaning
from app.pipeline.enrichment.fornecedores import enriquecer_com_fornecedor, extrair_cnpjs_distintos
from app.pipeline.ingestion import fornecedores, tce
from app.pipeline.persistence import tce_despesas as tce_despesas_persistence


class CompetenciasTceIndisponiveisError(RuntimeError):
    """Nenhuma competência solicitada possui uma publicação utilizável."""


TTL_COMPETENCIA_MES_ATUAL = timedelta(hours=1)
TTL_COMPETENCIA_TRES_MESES_ANTERIORES = timedelta(hours=24)
TTL_COMPETENCIA_HISTORICA = timedelta(days=7)


@dataclass(frozen=True)
class ResultadoAtualizacaoCompetenciasTce:
    publicadas: list[str]
    atualizadas: list[str]
    reutilizadas: list[str]
    desatualizadas: list[str]
    ausentes: list[str]
    falhas: list[dict[str, Any]]


def valor_json(valor: Any) -> Any:
    """Converte valores pandas/numpy em tipos aceitos por JSON."""
    if valor is None:
        return None

    try:
        nulo = pd.isna(valor)
    except (TypeError, ValueError):
        nulo = False

    try:
        if not isinstance(nulo, (list, tuple)) and not getattr(nulo, "shape", None) and bool(nulo):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(valor, pd.Timestamp):
        return valor.isoformat()

    if isinstance(valor, dict):
        return {str(chave): valor_json(item) for chave, item in valor.items()}

    if isinstance(valor, (list, tuple)):
        return [valor_json(item) for item in valor]

    if hasattr(valor, "item") and not isinstance(valor, (str, bytes, bytearray)):
        try:
            return valor.item()
        except (AttributeError, TypeError, ValueError):
            return valor

    return valor


def dataframe_para_registros(df: pd.DataFrame | None, *, limite: int | None = None) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []

    if limite is not None:
        df = df.head(limite)

    return [
        {str(chave): valor_json(valor) for chave, valor in registro.items()}
        for registro in df.to_dict(orient="records")
    ]


def tabelas_para_json(
    tabelas: dict[str, pd.DataFrame],
    *,
    limite: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    return {nome: dataframe_para_registros(tabela, limite=limite) for nome, tabela in tabelas.items()}


def _totais_tabelas(tabelas: dict[str, pd.DataFrame]) -> dict[str, int]:
    return {nome: int(len(tabela)) for nome, tabela in tabelas.items()}


def _ordenar_tce_contratos_por_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "data_contrato" not in df.columns:
        return df

    coluna_data_ordenacao = "_data_contrato_ordenacao"
    ordenado = df.copy()
    ordenado[coluna_data_ordenacao] = pd.to_datetime(ordenado["data_contrato"], errors="coerce", utc=True)

    colunas_ordenacao = [coluna_data_ordenacao]
    ascendentes = [True]
    if "numero_contrato" in ordenado.columns:
        colunas_ordenacao.append("numero_contrato")
        ascendentes.append(True)
    if "codigo_municipio" in ordenado.columns:
        colunas_ordenacao.append("codigo_municipio")
        ascendentes.append(True)

    return (
        ordenado.sort_values(colunas_ordenacao, ascending=ascendentes, na_position="last")
        .drop(columns=[coluna_data_ordenacao])
        .reset_index(drop=True)
    )


def _resolver_nome_municipio_tce(codigo_municipio: str) -> str:
    municipio_salvo = database.localizar_municipio_tce(codigo_municipio)
    if municipio_salvo is not None:
        return municipio_salvo["nome"]

    municipios = tce.buscar_municipios()
    database.salvar_municipios_tce(municipios, UF_PADRAO)
    codigo_procurado = str(codigo_municipio).strip()

    for municipio in municipios:
        codigo = str(municipio.get("codigo_municipio", "")).strip()
        if codigo != codigo_procurado:
            continue

        nome = municipio.get("nome_municipio") or municipio.get("nome") or municipio.get("municipio")
        if isinstance(nome, str) and nome.strip():
            return nome.strip()

    raise kpis.DadosInsuficientesKPI(
        f"[INDISPONIVEL] Nao foi possivel resolver o municipio TCE pelo codigo {codigo_municipio}."
    )


def _coletar_fornecedores(cnpjs: list[str], throttle_segundos: float) -> pd.DataFrame | None:
    if not cnpjs:
        return None

    return fornecedores.obter_fornecedores_com_cache(
        cnpjs,
        throttle_segundos=throttle_segundos,
    )


def _competencias_entre_datas(data_inicial: str, data_final: str) -> list[str]:
    try:
        inicio = date.fromisoformat(data_inicial)
        fim = date.fromisoformat(data_final)
    except (TypeError, ValueError) as error:
        raise ValueError("Datas devem usar o formato YYYY-MM-DD.") from error
    if inicio > fim:
        raise ValueError("data_inicial deve ser menor ou igual a data_final.")

    competencias: list[str] = []
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        competencias.append(f"{ano:04d}-{mes:02d}")
        if mes == 12:
            ano, mes = ano + 1, 1
        else:
            mes += 1
    return competencias


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ttl_competencia(competencia: str, agora: datetime) -> timedelta:
    ano, mes = (int(parte) for parte in competencia.split("-", maxsplit=1))
    distancia_meses = (agora.year - ano) * 12 + agora.month - mes
    if distancia_meses == 0:
        return TTL_COMPETENCIA_MES_ATUAL
    if 1 <= distancia_meses <= 3:
        return TTL_COMPETENCIA_TRES_MESES_ANTERIORES
    return TTL_COMPETENCIA_HISTORICA


def _publicacao_atualizada(
    competencia: str,
    publicado_em: datetime | None,
    agora: datetime,
) -> bool:
    if publicado_em is None:
        return False
    instante_publicacao = (
        publicado_em.replace(tzinfo=timezone.utc)
        if publicado_em.tzinfo is None
        else publicado_em.astimezone(timezone.utc)
    )
    return instante_publicacao >= agora - _ttl_competencia(competencia, agora)


def _atualizar_competencias_tce(
    *,
    competencias: list[str],
    codigo_municipio: str,
) -> ResultadoAtualizacaoCompetenciasTce:
    agora = _agora_utc()
    publicacoes_existentes = tce_despesas_persistence.listar_publicacoes_competencias(
        codigo_municipio_tce=codigo_municipio,
        competencias=competencias,
    )
    publicadas: list[str] = []
    atualizadas: list[str] = []
    reutilizadas: list[str] = []
    desatualizadas: list[str] = []
    ausentes: list[str] = []
    falhas: list[dict[str, Any]] = []
    for competencia in competencias:
        possui_publicacao = competencia in publicacoes_existentes
        if possui_publicacao and _publicacao_atualizada(
            competencia,
            publicacoes_existentes[competencia],
            agora,
        ):
            publicadas.append(competencia)
            reutilizadas.append(competencia)
            continue

        erro_atualizacao: str | None = None
        try:
            resultado = tce_despesas_pipeline.executar_ingestao_competencia_tce(
                competencia,
                codigo_municipio_tce=codigo_municipio,
            )
        except Exception as error:
            erro_atualizacao = str(error)
        else:
            if resultado.status != tce_despesas_persistence.STATUS_PUBLICADO:
                codigos = ", ".join(
                    str(problema.get("codigo", "ERRO_VALIDACAO"))
                    for problema in resultado.problemas
                )
                erro_atualizacao = codigos or f"Lote finalizado com status {resultado.status}."

        if erro_atualizacao is None:
            publicadas.append(competencia)
            atualizadas.append(competencia)
            continue

        usou_versao_publicada = possui_publicacao
        falhas.append(
            {
                "competencia": competencia,
                "erro": erro_atualizacao,
                "usou_versao_publicada": usou_versao_publicada,
            }
        )
        if usou_versao_publicada:
            publicadas.append(competencia)
            desatualizadas.append(competencia)
        else:
            ausentes.append(competencia)

    if not publicadas:
        meses = ", ".join(competencias)
        raise CompetenciasTceIndisponiveisError(
            f"Nenhuma competência do TCE-CE possui publicação utilizável: {meses}."
        )
    return ResultadoAtualizacaoCompetenciasTce(
        publicadas=publicadas,
        atualizadas=atualizadas,
        reutilizadas=reutilizadas,
        desatualizadas=desatualizadas,
        ausentes=ausentes,
        falhas=falhas,
    )


def montar_base_tce_contratos(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    enriquecer_fornecedores: bool = False,
    throttle_fornecedores: float = 0.3,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    contratos_brutos = tce.buscar_contratos(data_inicial, data_final, codigo_municipio=codigo_municipio)
    contratados_brutos = tce.buscar_contratados(data_inicial, data_final, codigo_municipio=codigo_municipio)

    df_contratos = tce_cleaning.limpar(contratos_brutos, chave_duplicata=["numero_contrato", "codigo_municipio"])
    df_contratados = tce_cleaning.limpar(
        contratados_brutos,
        chave_duplicata=["numero_contrato", "codigo_municipio", "numero_documento_negociante"],
    )

    fornecedores_df = None
    if enriquecer_fornecedores:
        serie_cnpj = (
            df_contratados["numero_documento_negociante"]
            if "numero_documento_negociante" in df_contratados.columns
            else None
        )
        cnpjs = extrair_cnpjs_distintos(serie_cnpj)
        fornecedores_df = _coletar_fornecedores(cnpjs, throttle_fornecedores)

    base = merge.montar_base_tce(df_contratos, df_contratados, fornecedores_df=fornecedores_df)
    base = _ordenar_tce_contratos_por_data(base)
    metadados = {
        "fonte": "TCE-CE",
        "parametros": {
            "data_inicial": data_inicial,
            "data_final": data_final,
            "codigo_municipio": codigo_municipio,
            "enriquecer_fornecedores": enriquecer_fornecedores,
        },
        "totais_brutos": {
            "contratos": len(contratos_brutos),
            "contratados": len(contratados_brutos),
        },
    }
    return base, metadados


def consultar_tce_contratos(*, limite: int | None = 100, **kwargs: Any) -> dict[str, Any]:
    base, metadados = montar_base_tce_contratos(**kwargs)
    return {
        **metadados,
        "limite_resposta": limite,
        "totais": {"contratos": int(len(base))},
        "dados": dataframe_para_registros(base, limite=limite),
    }


def montar_base_analitica_tce(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    municipio_comprador: str | None = None,
    throttle_fornecedores: float = 0.3,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    competencias = _competencias_entre_datas(data_inicial, data_final)
    atualizacao = _atualizar_competencias_tce(
        competencias=competencias,
        codigo_municipio=codigo_municipio,
    )
    empenhos = tce_despesas_persistence.listar_empenhos_liquidos_publicados(
        codigo_municipio_tce=codigo_municipio,
        data_inicial=date.fromisoformat(data_inicial),
        data_final=date.fromisoformat(data_final),
    )
    competencias_validas = set(atualizacao.publicadas)
    empenhos = [
        empenho
        for empenho in empenhos
        if str(empenho.get("data_empenho", ""))[:7] in competencias_validas
    ]

    municipio_comprador = municipio_comprador or _resolver_nome_municipio_tce(codigo_municipio)
    base = pd.DataFrame(empenhos)
    fornecedores_df = None
    if not base.empty:
        cnpjs = extrair_cnpjs_distintos(base.get("cnpj_fornecedor"))
        fornecedores_df = _coletar_fornecedores(cnpjs, throttle_fornecedores)
        if fornecedores_df is not None:
            base = enriquecer_com_fornecedor(
                base,
                fornecedores_df,
                coluna_cnpj="cnpj_fornecedor",
            )

    base_analitica = analitico.montar_base_analitica_tce(
        base,
        codigo_municipio=codigo_municipio,
        municipio_comprador=municipio_comprador,
        uf_comprador=UF_PADRAO,
    )
    metadados = {
        "fonte": "TCE-CE",
        "parametros": {
            "data_inicial": data_inicial,
            "data_final": data_final,
            "codigo_municipio": codigo_municipio,
        },
        "competencias_solicitadas": competencias,
        "competencias_publicadas": atualizacao.publicadas,
        "competencias_atualizadas": atualizacao.atualizadas,
        "competencias_reutilizadas": atualizacao.reutilizadas,
        "competencias_desatualizadas": atualizacao.desatualizadas,
        "competencias_ausentes": atualizacao.ausentes,
        "falhas_competencias": atualizacao.falhas,
        "politica_atualizacao": {
            "ttl_mes_atual_segundos": int(TTL_COMPETENCIA_MES_ATUAL.total_seconds()),
            "ttl_tres_meses_anteriores_segundos": int(
                TTL_COMPETENCIA_TRES_MESES_ANTERIORES.total_seconds()
            ),
            "ttl_historico_segundos": int(TTL_COMPETENCIA_HISTORICA.total_seconds()),
            "usar_publicacao_anterior_em_falha": True,
        },
        "totais_brutos": {"empenhos": len(empenhos)},
        "base_analitica": {
            "nome": "tce_empenhos_liquidos",
            "base_calculo": analitico.BASE_CALCULO_EMPENHOS_TCE,
            "municipio_comprador": municipio_comprador,
            "colunas": list(analitico.COLUNAS_BASE_ANALITICA_TCE),
        },
    }
    return base_analitica, metadados


def montar_indicadores_analiticos_tce(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    municipio_comprador: str | None = None,
    throttle_fornecedores: float = 0.3,
    limite_ranking: int = 10,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    base, metadados = montar_base_analitica_tce(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
        municipio_comprador=municipio_comprador,
        throttle_fornecedores=throttle_fornecedores,
    )
    indicadores = kpis.calcular_indicadores_analiticos_principais(
        base,
        limite_ranking=limite_ranking,
    )
    metadados = {
        **metadados,
        "indicadores_analiticos": {
            "escopo": "principais",
            "nomes": list(kpis.INDICADORES_ANALITICOS_PRINCIPAIS),
        },
    }
    return indicadores, metadados


def consultar_tce_indicadores_analiticos(
    *,
    limite: int | None = 100,
    limite_ranking: int = 10,
    **kwargs: Any,
) -> dict[str, Any]:
    indicadores, metadados = montar_indicadores_analiticos_tce(
        **kwargs,
        limite_ranking=limite_ranking,
    )
    return {
        **metadados,
        "limite_resposta": limite,
        "kpi": "indicadores_analiticos_principais",
        "totais": _totais_tabelas(indicadores),
        "indicadores": tabelas_para_json(indicadores, limite=limite),
    }


def consultar_kpi_tce_portes_por_mes(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    throttle_fornecedores: float = 0.3,
    limite: int | None = 100,
) -> dict[str, Any]:
    base, metadados = montar_base_analitica_tce(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
        throttle_fornecedores=throttle_fornecedores,
    )
    resultado = kpis.calcular_participacao_por_porte_por_mes(
        base,
        coluna_data="data_referencia",
        coluna_valor="valor",
        coluna_porte="porte_fornecedor",
    )
    return {
        **metadados,
        "limite_resposta": limite,
        "totais": {
            "empenhos": int(len(base)),
            "registros_kpi": int(len(resultado)),
        },
        "kpi": "participacao_por_porte_por_mes",
        "dados": dataframe_para_registros(resultado, limite=limite),
    }


def consultar_overview_tce(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    throttle_fornecedores: float = 0.3,
) -> dict[str, Any]:
    base, metadados = montar_base_analitica_tce(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
        throttle_fornecedores=throttle_fornecedores,
    )
    overview = kpis.calcular_overview_me_mei(base)
    return {
        **metadados,
        "kpi": "overview_me_mei",
        "escopo": {
            "portes_considerados": list(kpis.PORTES_OVERVIEW),
            "portes_no_denominador_participacao": list(
                kpis.PORTES_COMPRAS_CONSIDERADAS_OVERVIEW
            ),
            "regra_participacao_me": "(ME + MEI) / (ME + MEI + EPP + DEMAIS)",
            "regra_geografica": "ME + MEI",
        },
        "unidade_monetaria": "CENTAVOS",
        **overview,
    }

"""Orquestra coleta, normalização e publicação mensal das despesas do TCE-CE."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from app.pipeline.cleaners import tce_despesas as cleaner
from app.pipeline.ingestion import tce
from app.pipeline.persistence import tce_despesas as persistence


logger = logging.getLogger(__name__)


def executar_ingestao_competencia_tce(
    competencia: str,
    *,
    codigo_municipio_tce: str,
) -> persistence.ResultadoPublicacaoTce:
    """Publica os dois endpoints DCD como um único lote versionado."""
    inicio = datetime.now(timezone.utc)
    logger.info(
        "Ingestão da competência do TCE-CE iniciada",
        extra={
            "competencia": competencia,
            "codigo_municipio": codigo_municipio_tce,
        },
    )
    try:
        empenhos_brutos = tce.buscar_notas_empenhos(
            competencia,
            codigo_municipio=codigo_municipio_tce,
        )
        anulacoes_brutas = tce.buscar_notas_anulacoes_empenhos(
            competencia,
            codigo_municipio=codigo_municipio_tce,
        )
        empenhos = cleaner.mapear_notas_empenhos(empenhos_brutos)
        anulacoes = cleaner.mapear_notas_anulacoes_empenhos(anulacoes_brutas)
    except Exception as error:
        logger.error(
            "Falha na coleta ou normalização da competência do TCE-CE",
            extra={
                "competencia": competencia,
                "codigo_municipio": codigo_municipio_tce,
                "error_type": type(error).__name__,
            },
            exc_info=True,
        )
        persistence.registrar_lote_rejeitado(
            codigo_municipio_tce=codigo_municipio_tce,
            competencia=competencia,
            erro=str(error),
            iniciado_em=inicio,
        )
        raise

    try:
        resultado = persistence.publicar_lote_tce(
            codigo_municipio_tce=codigo_municipio_tce,
            competencia=competencia,
            empenhos=empenhos,
            anulacoes=anulacoes,
            iniciado_em=inicio,
        )
    except Exception as error:
        logger.error(
            "Falha ao persistir competência do TCE-CE",
            extra={
                "competencia": competencia,
                "codigo_municipio": codigo_municipio_tce,
                "error_type": type(error).__name__,
            },
            exc_info=True,
        )
        raise

    nivel = logging.INFO if resultado.status == persistence.STATUS_PUBLICADO else logging.WARNING
    logger.log(
        nivel,
        "Ingestão da competência do TCE-CE concluída",
        extra={
            "competencia": competencia,
            "codigo_municipio": codigo_municipio_tce,
            "run_id": resultado.run_id,
            "status": resultado.status,
            "empenhos_count": resultado.quantidade_empenhos,
            "anulacoes_count": resultado.quantidade_anulacoes,
            "problems_count": len(resultado.problemas),
            "previous_run_id": resultado.run_anterior_id,
        },
    )
    return resultado


__all__ = ["executar_ingestao_competencia_tce"]

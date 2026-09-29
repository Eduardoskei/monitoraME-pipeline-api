"""Orquestra coleta, normalização e publicação mensal das despesas do TCE-CE."""

from __future__ import annotations

from datetime import datetime, timezone

from app.pipeline.cleaners import tce_despesas as cleaner
from app.pipeline.ingestion import tce
from app.pipeline.persistence import tce_despesas as persistence


def executar_ingestao_competencia_tce(
    competencia: str,
    *,
    codigo_municipio_tce: str,
) -> persistence.ResultadoPublicacaoTce:
    """Publica os dois endpoints DCD como um único lote versionado."""
    inicio = datetime.now(timezone.utc)
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
        persistence.registrar_lote_rejeitado(
            codigo_municipio_tce=codigo_municipio_tce,
            competencia=competencia,
            erro=str(error),
            iniciado_em=inicio,
        )
        raise

    return persistence.publicar_lote_tce(
        codigo_municipio_tce=codigo_municipio_tce,
        competencia=competencia,
        empenhos=empenhos,
        anulacoes=anulacoes,
        iniciado_em=inicio,
    )


__all__ = ["executar_ingestao_competencia_tce"]

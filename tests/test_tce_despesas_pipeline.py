from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("PNCP_CONSULTA_BASE_URL", "https://pncp.gov.br/api/consulta")
os.environ.setdefault("PNCP_GESTAO_BASE_URL", "https://pncp.gov.br/api/pncp")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_IBGE_PADRAO", "2304400")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")
os.environ.setdefault("MODALIDADE_ID_PADRAO", "6")

from app.pipeline import tce_despesas


class PipelineTceDespesasTest(unittest.TestCase):
    @patch("app.pipeline.tce_despesas.persistence.publicar_lote_tce")
    @patch("app.pipeline.tce_despesas.cleaner.mapear_notas_anulacoes_empenhos")
    @patch("app.pipeline.tce_despesas.cleaner.mapear_notas_empenhos")
    @patch("app.pipeline.tce_despesas.tce.buscar_notas_anulacoes_empenhos")
    @patch("app.pipeline.tce_despesas.tce.buscar_notas_empenhos")
    def test_coleta_mapeia_e_publica_os_dois_endpoints_como_um_lote(
        self,
        buscar_empenhos,
        buscar_anulacoes,
        mapear_empenhos,
        mapear_anulacoes,
        publicar,
    ) -> None:
        buscar_empenhos.return_value = [{"bruto": "empenho"}]
        buscar_anulacoes.return_value = [{"bruto": "anulacao"}]
        mapear_empenhos.return_value = [{"normalizado": "empenho"}]
        mapear_anulacoes.return_value = [{"normalizado": "anulacao"}]
        esperado = MagicMock()
        publicar.return_value = esperado

        resultado = tce_despesas.executar_ingestao_competencia_tce(
            "2025-01",
            codigo_municipio_tce="010",
        )

        self.assertIs(resultado, esperado)
        buscar_empenhos.assert_called_once_with("2025-01", codigo_municipio="010")
        buscar_anulacoes.assert_called_once_with("2025-01", codigo_municipio="010")
        mapear_empenhos.assert_called_once_with(buscar_empenhos.return_value)
        mapear_anulacoes.assert_called_once_with(buscar_anulacoes.return_value)
        self.assertEqual(publicar.call_args.kwargs["empenhos"], mapear_empenhos.return_value)
        self.assertEqual(publicar.call_args.kwargs["anulacoes"], mapear_anulacoes.return_value)

    @patch("app.pipeline.tce_despesas.persistence.registrar_lote_rejeitado")
    @patch("app.pipeline.tce_despesas.persistence.publicar_lote_tce")
    @patch("app.pipeline.tce_despesas.tce.buscar_notas_anulacoes_empenhos")
    @patch("app.pipeline.tce_despesas.tce.buscar_notas_empenhos")
    def test_falha_de_coleta_registra_rejeicao_e_preserva_publicado(
        self,
        buscar_empenhos,
        buscar_anulacoes,
        publicar,
        registrar_rejeitado,
    ) -> None:
        buscar_empenhos.return_value = []
        buscar_anulacoes.side_effect = RuntimeError("TCE indisponível")

        with self.assertRaisesRegex(RuntimeError, "TCE indisponível"):
            tce_despesas.executar_ingestao_competencia_tce(
                "2025-01",
                codigo_municipio_tce="010",
            )

        publicar.assert_not_called()
        self.assertEqual(registrar_rejeitado.call_args.kwargs["competencia"], "2025-01")
        self.assertEqual(registrar_rejeitado.call_args.kwargs["erro"], "TCE indisponível")

    @patch("app.pipeline.tce_despesas.persistence.registrar_lote_rejeitado")
    @patch("app.pipeline.tce_despesas.persistence.publicar_lote_tce")
    @patch("app.pipeline.tce_despesas.cleaner.mapear_notas_anulacoes_empenhos", return_value=[])
    @patch("app.pipeline.tce_despesas.cleaner.mapear_notas_empenhos", return_value=[])
    @patch("app.pipeline.tce_despesas.tce.buscar_notas_anulacoes_empenhos", return_value=[])
    @patch("app.pipeline.tce_despesas.tce.buscar_notas_empenhos", return_value=[])
    def test_falha_transacional_da_publicacao_nao_e_mascarada_por_novo_registro(
        self,
        buscar_empenhos,
        buscar_anulacoes,
        mapear_empenhos,
        mapear_anulacoes,
        publicar,
        registrar_rejeitado,
    ) -> None:
        publicar.side_effect = RuntimeError("banco indisponível")

        with self.assertRaisesRegex(RuntimeError, "banco indisponível"):
            tce_despesas.executar_ingestao_competencia_tce(
                "2025-01",
                codigo_municipio_tce="010",
            )

        registrar_rejeitado.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
import requests
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("PNCP_CONSULTA_BASE_URL", "https://pncp.gov.br/api/consulta")
os.environ.setdefault("PNCP_GESTAO_BASE_URL", "https://pncp.gov.br/api/pncp")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://kitana.opencnpj.com")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_IBGE_PADRAO", "2304400")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")
os.environ.setdefault("MODALIDADE_ID_PADRAO", "6")

from app.pipeline.ingestion import tce


class TceIngestionTest(unittest.TestCase):
    def test_normalizar_data_tce_aceita_formato_compacto(self) -> None:
        self.assertEqual(tce.normalizar_data_tce("20250107"), "2025-01-07")
        self.assertEqual(tce.normalizar_data_tce("2025-01-07"), "2025-01-07")

    def test_normalizar_data_tce_rejeita_tipo_nao_string(self) -> None:
        with self.assertRaises(TypeError):
            tce.normalizar_data_tce(20250107)

    @patch("app.pipeline.ingestion.tce.buscar_dados_tce")
    def test_listar_registros_usa_start_index(self, buscar_dados_tce) -> None:
        buscar_dados_tce.side_effect = [
            {"elements": [{"numero_contrato": "1"}, {"numero_contrato": "2"}]},
            {"elements": [{"numero_contrato": "3"}]},
        ]

        tamanho_original = tce.TAMANHO_PAGINA
        tce.TAMANHO_PAGINA = 2

        try:
            registros = tce.listar_registros("contratos", {"codigo_municipio": "010"})
        finally:
            tce.TAMANHO_PAGINA = tamanho_original

        self.assertEqual([registro["numero_contrato"] for registro in registros], ["1", "2", "3"])
        self.assertEqual(buscar_dados_tce.call_args_list[0].args[1]["$start_index"], 0)
        self.assertEqual(buscar_dados_tce.call_args_list[1].args[1]["$start_index"], 2)

    @patch("app.pipeline.ingestion.pagination.time.sleep")
    @patch("app.pipeline.ingestion.tce.buscar_dados_tce")
    def test_listar_registros_municipio_alto_volume_pagina_ate_esgotar(
        self,
        buscar_dados_tce,
        sleep,
    ) -> None:
        def pagina(_endpoint: str, params: dict[str, object]) -> dict[str, object]:
            start_index = int(params["$start_index"])
            total = 2250
            fim = min(start_index + tce.TAMANHO_PAGINA, total)
            return {"elements": [{"numero_contrato": str(indice)} for indice in range(start_index, fim)]}

        buscar_dados_tce.side_effect = pagina

        registros = tce.listar_registros("contratos", {"codigo_municipio": "138"})

        self.assertEqual(len(registros), 2250)
        self.assertEqual(registros[0]["numero_contrato"], "0")
        self.assertEqual(registros[-1]["numero_contrato"], "2249")

        params_chamadas = [chamada.args[1] for chamada in buscar_dados_tce.call_args_list]
        self.assertEqual([params["$start_index"] for params in params_chamadas], [0, 1000, 2000])
        self.assertTrue(all(params["$count"] == 1000 for params in params_chamadas))
        self.assertTrue(all(params["codigo_municipio"] == "138" for params in params_chamadas))
        self.assertEqual(sleep.call_count, 2)

    @patch("app.core.logging.log_database.registrar_log_ingestao")
    @patch("app.pipeline.ingestion.tce.listar_registros")
    def test_buscar_contratacoes_filtra_modalidade_quando_informada(
        self,
        listar_registros,
        registrar_log_ingestao,
    ) -> None:
        listar_registros.return_value = [
            {"numero_licitacao": "A", "modalidade_licitacao": "6"},
            {"numero_licitacao": "B", "modalidade_licitacao": "9"},
        ]

        registros = tce.buscar_contratacoes("20250101", "20250107", modalidade="9")

        self.assertEqual(registros, [{"numero_licitacao": "B", "modalidade_licitacao": "9"}])
        registrar_log_ingestao.assert_called_once()

    @patch("app.core.logging.log_database.registrar_log_ingestao")
    @patch("app.pipeline.ingestion.tce.listar_registros")
    def test_buscar_contratos_grava_log_de_sucesso(
        self,
        listar_registros,
        registrar_log_ingestao,
    ) -> None:
        listar_registros.return_value = [{"numero_contrato": "2025000123"}]

        registros = tce.buscar_contratos("20250101", "20250131", codigo_municipio="010")

        self.assertEqual(registros, [{"numero_contrato": "2025000123"}])
        chamada = registrar_log_ingestao.call_args.kwargs
        self.assertEqual(chamada["fonte"], "TCE-CE")
        self.assertEqual(chamada["etapa"], "buscar_contratos")
        self.assertEqual(chamada["registros_processados"], 1)
        self.assertEqual(chamada["falhas_ocorridas"], 0)
        self.assertEqual(chamada["parametros"]["codigo_municipio"], "010")
        self.assertLessEqual(chamada["data_inicio"], chamada["data_termino"])

    @patch("app.pipeline.ingestion.tce.logger.warning")
    @patch("app.pipeline.ingestion.tce.time.sleep")
    @patch("app.core.logging.log_database.registrar_log_ingestao")
    @patch("app.pipeline.ingestion.tce.requests.get")
    def test_buscar_contratos_grava_log_de_falha_quando_tce_falha(
        self,
        get,
        registrar_log_ingestao,
        _sleep,
        _warning,
    ) -> None:
        get.side_effect = requests.ReadTimeout("TCE-CE demorou")

        registros = tce.buscar_contratos("20250101", "20250131", codigo_municipio="010")

        self.assertEqual(registros, [])
        chamada = registrar_log_ingestao.call_args.kwargs
        self.assertEqual(chamada["etapa"], "buscar_contratos")
        self.assertEqual(chamada["registros_processados"], 0)
        self.assertEqual(chamada["falhas_ocorridas"], 1)
        self.assertIn("TCE-CE demorou", chamada["erro"])


if __name__ == "__main__":
    unittest.main()

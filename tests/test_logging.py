from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
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

from app.core import logging as core_logging


class LoggingCoreTest(unittest.TestCase):
    @patch("app.core.logging.log_database.registrar_log_ingestao")
    def test_executar_com_log_ingestao_grava_sucesso(self, registrar_log_ingestao) -> None:
        resultado = core_logging.executar_com_log_ingestao(
            fonte="TCE-CE",
            etapa="buscar_contratos",
            parametros={"codigo_municipio": "010"},
            totais={"endpoint": "contratos"},
            executar=lambda: [{"numero_contrato": "2025000123"}],
        )

        self.assertEqual(resultado, [{"numero_contrato": "2025000123"}])
        chamada = registrar_log_ingestao.call_args.kwargs
        self.assertEqual(chamada["fonte"], "TCE-CE")
        self.assertEqual(chamada["etapa"], "buscar_contratos")
        self.assertEqual(chamada["registros_processados"], 1)
        self.assertEqual(chamada["falhas_ocorridas"], 0)
        self.assertEqual(chamada["totais"]["endpoint"], "contratos")
        self.assertEqual(chamada["totais"]["registros_processados"], 1)
        self.assertLessEqual(chamada["data_inicio"], chamada["data_termino"])

    @patch("app.core.logging.log_database.registrar_log_ingestao")
    def test_registrar_falha_ingestao_contabiliza_no_contexto(self, registrar_log_ingestao) -> None:
        def executar() -> list[dict[str, object]]:
            core_logging.registrar_falha_ingestao("timeout")
            return []

        resultado = core_logging.executar_com_log_ingestao(
            fonte="TCE-CE",
            etapa="buscar_contratos",
            parametros={},
            executar=executar,
        )

        self.assertEqual(resultado, [])
        chamada = registrar_log_ingestao.call_args.kwargs
        self.assertEqual(chamada["registros_processados"], 0)
        self.assertEqual(chamada["falhas_ocorridas"], 1)
        self.assertEqual(chamada["erro"], "timeout")


if __name__ == "__main__":
    unittest.main()

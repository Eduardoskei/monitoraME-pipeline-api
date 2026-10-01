from __future__ import annotations

import json
import logging
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
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from app.core import logging as core_logging


class LoggingCoreTest(unittest.TestCase):
    def test_json_formatter_inclui_contexto_e_redige_segredos(self) -> None:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Evento de teste",
            args=(),
            exc_info=None,
        )
        record.parametros = {
            "codigo_municipio": "010",
            "access_token": "nao-pode-vazar",
        }
        record.authorization = "Bearer nao-pode-vazar"
        payload = json.loads(core_logging.JsonFormatter().format(record))

        self.assertEqual(payload["message"], "Evento de teste")
        self.assertNotIn("timestamp", payload)
        self.assertNotIn("event", payload)
        self.assertNotIn("request_id", payload)
        self.assertEqual(payload["parametros"]["codigo_municipio"], "010")
        self.assertEqual(payload["parametros"]["access_token"], "[REDACTED]")
        self.assertEqual(payload["authorization"], "[REDACTED]")

    @patch("app.core.logging.log_database.registrar_log_aplicacao")
    def test_database_handler_persiste_apenas_contexto_relevante(
        self,
        registrar_log_aplicacao,
    ) -> None:
        record = logging.LogRecord(
            name="app.pipeline.ingestion.tce",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Tentativas esgotadas",
            args=(),
            exc_info=None,
        )
        record.source = "TCE-CE"
        record.attempts = 4
        record.api_key = "nao-pode-vazar"

        core_logging.DatabaseLogHandler().emit(record)

        chamada = registrar_log_aplicacao.call_args.kwargs
        self.assertEqual(chamada["nivel"], "ERROR")
        self.assertEqual(chamada["logger_nome"], "app.pipeline.ingestion.tce")
        self.assertEqual(chamada["mensagem"], "Tentativas esgotadas")
        self.assertEqual(
            chamada["contexto"],
            {"source": "TCE-CE", "attempts": 4, "api_key": "[REDACTED]"},
        )
        self.assertIsNone(chamada["excecao"])

    @patch(
        "app.core.logging.log_database.registrar_log_aplicacao",
        side_effect=RuntimeError("banco indisponivel"),
    )
    def test_database_handler_abre_circuito_quando_persistencia_falha(
        self,
        registrar_log_aplicacao,
    ) -> None:
        record = logging.LogRecord(
            name="app.test",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Alerta",
            args=(),
            exc_info=None,
        )
        handler = core_logging.DatabaseLogHandler()

        handler.emit(record)
        handler.emit(record)

        registrar_log_aplicacao.assert_called_once()

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

    @patch("app.core.logging.log_database.registrar_log_ingestao")
    def test_executar_com_log_ingestao_registra_falha_e_propaga_erro(
        self,
        registrar_log_ingestao,
    ) -> None:
        def falhar() -> list[dict[str, object]]:
            raise RuntimeError("fonte indisponivel")

        with self.assertLogs("app.core.logging", level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "fonte indisponivel"):
                core_logging.executar_com_log_ingestao(
                    fonte="TCE-CE",
                    etapa="buscar_dados",
                    parametros={},
                    executar=falhar,
                )

        chamada = registrar_log_ingestao.call_args.kwargs
        self.assertEqual(chamada["falhas_ocorridas"], 1)
        self.assertEqual(chamada["erro"], "fonte indisponivel")
        self.assertTrue(any("Ingestão interrompida" in item for item in logs.output))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
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

from app.core import log_database


class FakeCursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.executions.append((sql, params))


class FakeConn:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_obj


class LogDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        log_database._log_schema_initialized = False

    def tearDown(self) -> None:
        log_database._log_schema_initialized = False

    def test_init_log_db_cria_tabela_e_indices(self) -> None:
        conn = FakeConn()

        with (
            patch("app.core.log_database.get_log_conn", return_value=conn),
            patch("app.core.log_database.put_log_conn") as put_log_conn,
        ):
            log_database.init_log_db()

        sqls = [sql for sql, _params in conn.cursor_obj.executions]
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS logs_ingestao" in sql for sql in sqls))
        self.assertTrue(any("idx_logs_ingestao_fonte_inicio" in sql for sql in sqls))
        self.assertTrue(any("idx_logs_ingestao_etapa_status" in sql for sql in sqls))
        put_log_conn.assert_called_once_with(conn)

    def test_registrar_log_ingestao_insere_volume_falhas_e_metadados(self) -> None:
        conn = FakeConn()
        log_database._log_schema_initialized = True
        inicio = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
        termino = datetime(2026, 8, 27, 10, 5, tzinfo=timezone.utc)

        with (
            patch("app.core.log_database.get_log_conn", return_value=conn),
            patch("app.core.log_database.put_log_conn"),
        ):
            log_database.registrar_log_ingestao(
                fonte="TCE-CE",
                etapa="buscar_contratos",
                data_inicio=inicio,
                data_termino=termino,
                registros_processados=7,
                falhas_ocorridas=1,
                parametros={"codigo_municipio": "010"},
                totais={"contratos": 7},
                erro="timeout",
            )

        sql, params = conn.cursor_obj.executions[0]
        self.assertIn("INSERT INTO logs_ingestao", sql)
        self.assertIsNotNone(params)
        assert params is not None
        self.assertEqual(params[0], "TCE-CE")
        self.assertEqual(params[1], "buscar_contratos")
        self.assertEqual(params[2], "falha")
        self.assertEqual(params[5], 7)
        self.assertEqual(params[6], 1)
        self.assertEqual(json.loads(str(params[7])), {"codigo_municipio": "010"})
        self.assertEqual(json.loads(str(params[8])), {"contratos": 7})
        self.assertEqual(params[9], "timeout")


if __name__ == "__main__":
    unittest.main()

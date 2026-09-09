from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://kitana.opencnpj.com")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_IBGE_PADRAO", "2304400")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")
os.environ.setdefault("MODALIDADE_ID_PADRAO", "6")

from app.core import log_database
from app.core import orm
from app.core.log_models import LogIngestao


class FakeConnection:
    def __init__(self) -> None:
        self.executions: list[object] = []

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object) -> None:
        self.executions.append(statement)


class FakeEngine:
    def __init__(self) -> None:
        self.connection = FakeConnection()

    def connect(self) -> FakeConnection:
        return self.connection


@contextmanager
def fake_log_session(session: MagicMock):
    yield session


class LogDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        log_database._log_schema_initialized = False

    def tearDown(self) -> None:
        orm.dispose_log_engine()
        log_database._log_schema_initialized = False

    def test_init_log_db_valida_conexao_do_engine_de_logs(self) -> None:
        engine = FakeEngine()

        with patch("app.core.log_database.orm.get_log_engine", return_value=engine) as get_log_engine:
            log_database.init_log_db()
            log_database.init_log_db()

        get_log_engine.assert_called_once_with()
        self.assertEqual(len(engine.connection.executions), 1)
        self.assertEqual(str(engine.connection.executions[0]), "SELECT 1")
        self.assertTrue(log_database._log_schema_initialized)

    @patch("app.core.log_database.orm.dispose_log_engine")
    def test_close_log_pool_descarta_engine_e_reseta_estado(self, dispose_log_engine) -> None:
        log_database._log_schema_initialized = True

        log_database.close_log_pool()

        dispose_log_engine.assert_called_once_with()
        self.assertFalse(log_database._log_schema_initialized)

    def test_registrar_log_ingestao_persiste_modelo_orm_com_metadados(self) -> None:
        session = MagicMock()
        inicio = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
        termino = datetime(2026, 8, 27, 10, 5, tzinfo=timezone.utc)
        log_database._log_schema_initialized = True

        with patch("app.core.log_database.orm.log_session", return_value=fake_log_session(session)):
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

        session.add.assert_called_once()
        registro = session.add.call_args.args[0]
        self.assertIsInstance(registro, LogIngestao)
        self.assertEqual(registro.fonte, "TCE-CE")
        self.assertEqual(registro.etapa, "buscar_contratos")
        self.assertEqual(registro.status, "falha")
        self.assertEqual(registro.data_inicio, inicio)
        self.assertEqual(registro.data_termino, termino)
        self.assertEqual(registro.registros_processados, 7)
        self.assertEqual(registro.falhas_ocorridas, 1)
        self.assertEqual(registro.parametros, {"codigo_municipio": "010"})
        self.assertEqual(registro.totais, {"contratos": 7})
        self.assertEqual(registro.erro, "timeout")

    def test_registrar_log_ingestao_normaliza_datas_naive_contadores_e_json(self) -> None:
        session = MagicMock()
        inicio = datetime(2026, 8, 27, 10, 0)
        termino = datetime(2026, 8, 27, 8, 5, tzinfo=timezone(timedelta(hours=-3)))
        log_database._log_schema_initialized = True

        with patch("app.core.log_database.orm.log_session", return_value=fake_log_session(session)):
            log_database.registrar_log_ingestao(
                fonte="TCE-CE",
                etapa="buscar_contratos",
                data_inicio=inicio,
                data_termino=termino,
                registros_processados=-7,
                falhas_ocorridas=-1,
                parametros={"quando": inicio},
                totais=None,
            )

        registro = session.add.call_args.args[0]
        self.assertEqual(registro.status, "sucesso")
        self.assertEqual(registro.data_inicio, datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(registro.data_termino, datetime(2026, 8, 27, 11, 5, tzinfo=timezone.utc))
        self.assertEqual(registro.registros_processados, 0)
        self.assertEqual(registro.falhas_ocorridas, 0)
        self.assertEqual(registro.parametros, {"quando": "2026-08-27 10:00:00"})
        self.assertEqual(registro.totais, {})


if __name__ == "__main__":
    unittest.main()

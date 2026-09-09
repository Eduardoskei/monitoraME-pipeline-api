from __future__ import annotations

from datetime import datetime, timezone
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

from app.core import database


class FakeCursor:
    def __init__(self) -> None:
        self.executions: list[str] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, _params: tuple[object, ...] | None = None) -> None:
        self.executions.append(sql)


class FakeConn:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_obj


class DatabasePncpSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        database._schema_initialized = False

    def tearDown(self) -> None:
        database._schema_initialized = False

    def test_init_db_cria_tabelas_pncp_incrementais(self) -> None:
        conn = FakeConn()

        with (
            patch("app.core.database.get_conn", return_value=conn),
            patch("app.core.database.put_conn"),
        ):
            database.init_db()

        sql = "\n".join(conn.cursor_obj.executions)
        self.assertIn("CREATE TABLE IF NOT EXISTS pncp_ingestion_state", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS pncp_ingestion_runs", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS pncp_contratacoes", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS pncp_itens", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS pncp_resultados", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS pncp_contratos", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS pncp_pca_planos", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS pncp_pca_itens", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS pncp_fornecedores", sql)

    def test_registrar_sucesso_e_checkpoint_grava_execucao_e_estado_na_mesma_conexao(self) -> None:
        conn = FakeConn()
        database._schema_initialized = True
        instante = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)

        with (
            patch("app.core.database.get_conn", return_value=conn),
            patch("app.core.database.put_conn") as put_conn,
        ):
            database.registrar_sucesso_e_checkpoint_pncp(
                escopo="pncp:ufs=CE:modalidades=6",
                executado_em=instante,
                janela_inicio=instante,
                janela_fim=instante,
                quantidade_lida=2,
                quantidade_inserida=1,
                quantidade_atualizada=1,
                parametros={"uf": "CE"},
                totais={"contratacoes": 1},
            )

        sql = "\n".join(conn.cursor_obj.executions)
        self.assertIn("INSERT INTO pncp_ingestion_runs", sql)
        self.assertIn("INSERT INTO pncp_ingestion_state", sql)
        put_conn.assert_called_once_with(conn)


if __name__ == "__main__":
    unittest.main()

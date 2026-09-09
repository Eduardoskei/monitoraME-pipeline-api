from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

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

from app.pipeline.persistence import pncp as pncp_persistence


class FakeCursor:
    def __init__(self, retornos: list[tuple[bool]]) -> None:
        self.retornos = retornos
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.executions.append((sql, params))

    def fetchone(self) -> tuple[bool]:
        return self.retornos.pop(0)


class FakeConn:
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor_obj = cursor

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_obj


class PncpPersistenceTest(unittest.TestCase):
    def test_persistir_tabelas_usa_upsert_e_contabiliza_insert_update(self) -> None:
        cursor = FakeCursor(retornos=[(True,), (False,)])
        conn = FakeConn(cursor)
        tabelas = {
            "contratacoes": pd.DataFrame(
                [
                    {
                        "numero_controle_pncp": "12345678000199-1-000001/2026",
                        "orgao_entidade_cnpj": "12.345.678/0001-99",
                        "ano_compra": 2026,
                        "sequencial_compra": 1,
                        "objeto_compra": "Material de consumo",
                    },
                    {
                        "numero_controle_pncp": "12345678000199-1-000002/2026",
                        "orgao_entidade_cnpj": "12.345.678/0001-99",
                        "ano_compra": 2026,
                        "sequencial_compra": 2,
                        "objeto_compra": "Material de consumo",
                    },
                ]
            )
        }

        with (
            patch("app.pipeline.persistence.pncp.database.init_db"),
            patch("app.pipeline.persistence.pncp.database.get_conn", return_value=conn),
            patch("app.pipeline.persistence.pncp.database.put_conn") as put_conn,
        ):
            resultado = pncp_persistence.persistir_tabelas(tabelas)

        self.assertEqual(resultado.inseridos, 1)
        self.assertEqual(resultado.atualizados, 1)
        self.assertEqual(resultado.por_tabela["contratacoes"], {"inseridos": 1, "atualizados": 1})
        self.assertTrue(all("ON CONFLICT" in sql for sql, _params in cursor.executions))
        self.assertEqual(cursor.executions[0][1][0], "12345678000199-1-000001/2026")
        self.assertEqual(cursor.executions[0][1][1], "12345678000199")
        put_conn.assert_called_once_with(conn)


if __name__ == "__main__":
    unittest.main()

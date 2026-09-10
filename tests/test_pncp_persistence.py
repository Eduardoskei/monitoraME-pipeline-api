from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy.dialects import postgresql

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


class FakeResult:
    def __init__(self, inserido: bool) -> None:
        self.inserido = inserido

    def scalar_one(self) -> bool:
        return self.inserido


class FakeSession:
    def __init__(self, retornos: list[bool]) -> None:
        self.retornos = retornos
        self.statements: list[object] = []

    def execute(self, statement: object) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self.retornos.pop(0))


@contextmanager
def fake_main_session(session: FakeSession):
    yield session


def _compiled_sql(statement: object) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def _compiled_params(statement: object) -> dict[str, object]:
    return statement.compile(dialect=postgresql.dialect()).params


class PncpPersistenceTest(unittest.TestCase):
    def test_persistir_tabelas_usa_upsert_sqlalchemy_e_contabiliza_insert_update(self) -> None:
        session = FakeSession(retornos=[True, False])
        tabelas = {
            "contratacoes": pd.DataFrame(
                [
                    {
                        "numero_controle_pncp": "12345678000199-1-000001/2026",
                        "orgao_entidade_cnpj": "12.345.678/0001-99",
                        "ano_compra": 2026,
                        "sequencial_compra": 1,
                        "objeto_compra": "Material de consumo",
                        "data_publicacao_pncp": "2026-09-09",
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
            patch("app.pipeline.persistence.pncp.database.init_db") as init_db,
            patch("app.pipeline.persistence.pncp.orm.main_session", return_value=fake_main_session(session)),
        ):
            resultado = pncp_persistence.persistir_tabelas(tabelas)

        self.assertEqual(resultado.inseridos, 1)
        self.assertEqual(resultado.atualizados, 1)
        self.assertEqual(resultado.por_tabela["contratacoes"], {"inseridos": 1, "atualizados": 1})
        init_db.assert_called_once_with()

        sql = _compiled_sql(session.statements[0])
        params = _compiled_params(session.statements[0])
        self.assertIn("INSERT INTO pncp_contratacoes", sql)
        self.assertIn("ON CONFLICT (numero_controle_pncp) DO UPDATE", sql)
        self.assertIn("RETURNING (xmax = 0) AS inserido", sql)
        self.assertEqual(params["numero_controle_pncp"], "12345678000199-1-000001/2026")
        self.assertEqual(params["cnpj_orgao"], "12345678000199")
        self.assertEqual(params["data_publicacao_pncp"], datetime(2026, 9, 9, tzinfo=timezone.utc))
        self.assertIsInstance(params["payload"], dict)

    def test_persistir_tabelas_persiste_todas_as_tabelas_pncp_sem_cursor(self) -> None:
        session = FakeSession(retornos=[True] * 7)
        tabelas = {
            "contratacoes": pd.DataFrame(
                [
                    {
                        "numero_controle_pncp": "12345678000199-1-000001/2026",
                        "cnpj_orgao": "12345678000199",
                    }
                ]
            ),
            "itens": pd.DataFrame(
                [
                    {
                        "numero_controle_pncp": "12345678000199-1-000001/2026",
                        "numero_item": 1,
                        "descricao": "Cafe",
                        "valor_total": "10.50",
                    }
                ]
            ),
            "resultados": pd.DataFrame(
                [
                    {
                        "numero_controle_pncp": "12345678000199-1-000001/2026",
                        "numero_item": 1,
                        "sequencial_resultado": 1,
                        "ni_fornecedor": "12.345.678/0001-99",
                        "valor_total_homologado": 9.5,
                    }
                ]
            ),
            "contratos": pd.DataFrame(
                [
                    {
                        "numero_controle_pncp": "12345678000199-1-000001/2026",
                        "ano_contrato": 2026,
                        "sequencial_contrato": 1,
                        "numero_contrato_empenho": "CT-1",
                        "valor_global": 9.5,
                    }
                ]
            ),
            "pca_planos": pd.DataFrame(
                [
                    {
                        "cnpj": "12.345.678/0001-99",
                        "ano_pca": 2026,
                        "sequencial_pca": 3,
                        "municipio": "Fortaleza",
                        "uf": "CE",
                    }
                ]
            ),
            "pca_itens": pd.DataFrame(
                [
                    {
                        "cnpj": "12.345.678/0001-99",
                        "ano_pca": 2026,
                        "sequencial_pca": 3,
                        "numero_item": 4,
                        "descricao": "Notebook",
                        "quantidade": 2,
                    }
                ]
            ),
            "fornecedores": pd.DataFrame(
                [
                    {
                        "cnpj": "12.345.678/0001-99",
                        "razao_social": "EMPRESA TESTE LTDA",
                        "porte": "ME",
                        "elegivel_me": True,
                    }
                ]
            ),
        }

        with (
            patch("app.pipeline.persistence.pncp.database.init_db"),
            patch("app.pipeline.persistence.pncp.orm.main_session", return_value=fake_main_session(session)),
        ):
            resultado = pncp_persistence.persistir_tabelas(tabelas)

        self.assertEqual(resultado.inseridos, 7)
        self.assertEqual(resultado.atualizados, 0)
        self.assertEqual(
            [_compiled_params(statement).get("item_id") for statement in session.statements if "item_id" in _compiled_params(statement)],
            ["pncp-item:12345678000199-1-000001/2026:1"],
        )

        nomes_tabelas = [statement.table.name for statement in session.statements]
        self.assertEqual(
            nomes_tabelas,
            [
                "pncp_contratacoes",
                "pncp_itens",
                "pncp_resultados",
                "pncp_contratos",
                "pncp_pca_planos",
                "pncp_pca_itens",
                "pncp_fornecedores",
            ],
        )

        item_params = _compiled_params(session.statements[1])
        fornecedor_params = _compiled_params(session.statements[-1])
        self.assertEqual(item_params["valor_total"], Decimal("10.50"))
        self.assertTrue(fornecedor_params["elegivel_me"])

    def test_persistir_tabelas_ignora_registros_sem_chave_obrigatoria(self) -> None:
        session = FakeSession(retornos=[])
        tabelas = {
            "contratacoes": pd.DataFrame([{"objeto_compra": "sem chave"}]),
            "itens": pd.DataFrame([{"numero_item": 1}]),
            "resultados": pd.DataFrame([{"numero_item": 1}]),
            "contratos": pd.DataFrame([{"ano_contrato": 2026}]),
            "fornecedores": pd.DataFrame([{"cnpj": "invalido"}]),
        }

        with (
            patch("app.pipeline.persistence.pncp.database.init_db"),
            patch("app.pipeline.persistence.pncp.orm.main_session", return_value=fake_main_session(session)),
        ):
            resultado = pncp_persistence.persistir_tabelas(tabelas)

        self.assertEqual(resultado.inseridos, 0)
        self.assertEqual(resultado.atualizados, 0)
        self.assertEqual(session.statements, [])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects.postgresql import JSONB

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

from app.core import log_models, models, orm


def _url_psycopg(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


class OrmMetadataTest(unittest.TestCase):
    def test_main_metadata_registra_tabelas_do_banco_principal(self) -> None:
        esperado = {
            "ibge_municipios",
            "fornecedores_me",
            "pncp_ingestion_state",
            "pncp_ingestion_runs",
            "pncp_contratacoes",
            "pncp_itens",
            "pncp_resultados",
            "pncp_contratos",
            "pncp_pca_planos",
            "pncp_pca_itens",
            "pncp_fornecedores",
        }

        self.assertEqual(set(orm.MainBase.metadata.tables), esperado)

    def test_modelo_contratacao_tem_jsonb_datas_e_relacionamentos(self) -> None:
        tabela = models.PncpContratacao.__table__

        self.assertIsInstance(tabela.c.payload.type, JSONB)
        self.assertFalse(tabela.c.payload.nullable)
        self.assertTrue(tabela.c.data_publicacao_pncp.type.timezone)
        self.assertIn("itens", models.PncpContratacao.__mapper__.relationships)
        self.assertIn("resultados", models.PncpContratacao.__mapper__.relationships)
        self.assertIn("contratos", models.PncpContratacao.__mapper__.relationships)

    def test_modelo_item_tem_fk_com_delete_cascade(self) -> None:
        fk = next(iter(models.PncpItem.__table__.foreign_keys))

        self.assertEqual(fk.column.table.name, "pncp_contratacoes")
        self.assertEqual(fk.ondelete, "CASCADE")

    def test_log_metadata_registra_apenas_tabela_de_logs(self) -> None:
        self.assertEqual(set(orm.LogBase.metadata.tables), {"logs_ingestao"})

        tabela = log_models.LogIngestao.__table__
        self.assertIsInstance(tabela.c.parametros.type, JSONB)
        self.assertFalse(tabela.c.registros_processados.nullable)
        self.assertTrue(tabela.c.data_inicio.type.timezone)


class OrmEngineFactoryTest(unittest.TestCase):
    def setUp(self) -> None:
        orm.dispose_engines()

    def tearDown(self) -> None:
        orm.dispose_engines()

    @patch("app.core.orm.create_engine")
    def test_main_engine_usa_database_url_sslmode_e_cache(self, create_engine) -> None:
        engine = MagicMock()
        create_engine.return_value = engine

        primeiro = orm.get_main_engine()
        segundo = orm.get_main_engine()

        self.assertIs(primeiro, engine)
        self.assertIs(segundo, engine)
        create_engine.assert_called_once()
        args, kwargs = create_engine.call_args
        self.assertEqual(args[0], _url_psycopg(os.environ["DATABASE_URL"]))
        self.assertEqual(kwargs["pool_size"], 10)
        self.assertEqual(kwargs["max_overflow"], 0)
        self.assertEqual(kwargs["connect_args"], {"sslmode": "require"})

    @patch("app.core.orm.create_engine")
    def test_log_engine_usa_log_database_url(self, create_engine) -> None:
        engine = MagicMock()
        create_engine.return_value = engine

        self.assertIs(orm.get_log_engine(), engine)

        args, kwargs = create_engine.call_args
        self.assertEqual(args[0], _url_psycopg(os.environ["LOG_DATABASE_URL"]))
        self.assertEqual(kwargs["connect_args"], {"sslmode": "require"})

    def test_urls_explicitas_com_driver_sao_preservadas(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:5432/main",
                "LOG_DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:5432/logs",
            },
        ):
            self.assertEqual(
                orm.main_database_url(),
                "postgresql+psycopg://postgres:postgres@localhost:5432/main",
            )
            self.assertEqual(
                orm.log_database_url(),
                "postgresql+psycopg://postgres:postgres@localhost:5432/logs",
            )

    def test_main_session_context_manager_commita_e_fecha(self) -> None:
        session = MagicMock()

        with patch("app.core.orm.new_main_session", return_value=session):
            with orm.main_session() as yielded:
                self.assertIs(yielded, session)

        session.commit.assert_called_once_with()
        session.rollback.assert_not_called()
        session.close.assert_called_once_with()

    def test_log_session_context_manager_faz_rollback_em_erro(self) -> None:
        session = MagicMock()

        with self.assertRaisesRegex(RuntimeError, "falhou"):
            with patch("app.core.orm.new_log_session", return_value=session):
                with orm.log_session():
                    raise RuntimeError("falhou")

        session.commit.assert_not_called()
        session.rollback.assert_called_once_with()
        session.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

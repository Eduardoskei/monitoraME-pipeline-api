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

from app.core import database, orm
from app.core.models import FornecedorMe, IbgeMunicipio, PncpIngestionRun, PncpIngestionState


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
def fake_main_session(session: MagicMock):
    yield session


class DatabasePncpTest(unittest.TestCase):
    def setUp(self) -> None:
        database._schema_initialized = False

    def tearDown(self) -> None:
        orm.dispose_main_engine()
        database._schema_initialized = False

    def test_init_db_valida_conexao_do_engine_principal(self) -> None:
        engine = FakeEngine()

        with patch("app.core.database.orm.get_main_engine", return_value=engine) as get_main_engine:
            database.init_db()
            database.init_db()

        get_main_engine.assert_called_once_with()
        self.assertEqual(len(engine.connection.executions), 1)
        self.assertEqual(str(engine.connection.executions[0]), "SELECT 1")
        self.assertTrue(database._schema_initialized)

    @patch("app.core.database.orm.dispose_main_engine")
    def test_close_pool_descarta_engine_e_reseta_estado(self, dispose_main_engine) -> None:
        database._schema_initialized = True

        database.close_pool()

        dispose_main_engine.assert_called_once_with()
        self.assertFalse(database._schema_initialized)

    def test_salvar_municipios_ibge_usa_modelos_orm_e_ignora_invalidos(self) -> None:
        session = MagicMock()
        database._schema_initialized = True
        municipios = [
            {"id": 2304400, "nome": "Fortaleza"},
            {"id": None, "nome": "Invalido"},
        ]

        with patch("app.core.database.orm.main_session", return_value=fake_main_session(session)):
            quantidade = database.salvar_municipios_ibge(municipios, "CE")

        self.assertEqual(quantidade, 1)
        session.merge.assert_called_once()
        municipio = session.merge.call_args.args[0]
        self.assertIsInstance(municipio, IbgeMunicipio)
        self.assertEqual(municipio.codigo_municipio, "2304400")
        self.assertEqual(municipio.nome, "Fortaleza")
        self.assertEqual(municipio.uf, "CE")

    def test_listar_municipios_ibge_serializa_modelos(self) -> None:
        session = MagicMock()
        database._schema_initialized = True
        session.scalars.return_value.all.return_value = [
            IbgeMunicipio(codigo_municipio="2301000", nome="Abaiara", uf="CE"),
            IbgeMunicipio(codigo_municipio="2304400", nome="Fortaleza", uf="CE"),
        ]

        with patch("app.core.database.orm.main_session", return_value=fake_main_session(session)):
            municipios = database.listar_municipios_ibge("CE")

        self.assertEqual(
            municipios,
            [
                {"id": "2301000", "codigo_municipio": "2301000", "nome": "Abaiara", "uf": "CE"},
                {"id": "2304400", "codigo_municipio": "2304400", "nome": "Fortaleza", "uf": "CE"},
            ],
        )
        session.scalars.assert_called_once()

    def test_localizar_municipio_ibge_usa_session_get(self) -> None:
        session = MagicMock()
        database._schema_initialized = True
        session.get.return_value = IbgeMunicipio(codigo_municipio="2304400", nome="Fortaleza", uf="CE")

        with patch("app.core.database.orm.main_session", return_value=fake_main_session(session)):
            municipio = database.localizar_municipio_ibge(2304400)

        session.get.assert_called_once_with(IbgeMunicipio, "2304400")
        self.assertEqual(municipio, {"id": "2304400", "codigo_municipio": "2304400", "nome": "Fortaleza", "uf": "CE"})

    def test_salvar_fornecedor_me_usa_modelo_orm(self) -> None:
        session = MagicMock()
        database._schema_initialized = True

        with patch("app.core.database.orm.main_session", return_value=fake_main_session(session)):
            database.salvar_fornecedor_me("12345678000199", "EMPRESA TESTE LTDA", "ME")

        fornecedor = session.merge.call_args.args[0]
        self.assertIsInstance(fornecedor, FornecedorMe)
        self.assertEqual(fornecedor.cnpj, "12345678000199")
        self.assertEqual(fornecedor.razao_social, "EMPRESA TESTE LTDA")
        self.assertEqual(fornecedor.porte, "ME")

    def test_localizar_fornecedor_me_usa_session_get(self) -> None:
        session = MagicMock()
        database._schema_initialized = True
        session.get.return_value = FornecedorMe(
            cnpj="12345678000199",
            razao_social="EMPRESA TESTE LTDA",
            porte="ME",
        )

        with patch("app.core.database.orm.main_session", return_value=fake_main_session(session)):
            fornecedor = database.localizar_fornecedor_me("12345678000199")

        session.get.assert_called_once_with(FornecedorMe, "12345678000199")
        self.assertEqual(
            fornecedor,
            {"cnpj": "12345678000199", "razao_social": "EMPRESA TESTE LTDA", "porte": "ME"},
        )

    def test_buscar_checkpoint_pncp_retorna_data_do_estado(self) -> None:
        session = MagicMock()
        database._schema_initialized = True
        instante = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)
        session.get.return_value = PncpIngestionState(
            escopo="pncp:ufs=CE:modalidades=6",
            ultima_execucao_sucesso=instante,
            parametros={},
        )

        with patch("app.core.database.orm.main_session", return_value=fake_main_session(session)):
            checkpoint = database.buscar_checkpoint_pncp("pncp:ufs=CE:modalidades=6")

        session.get.assert_called_once_with(PncpIngestionState, "pncp:ufs=CE:modalidades=6")
        self.assertEqual(checkpoint, instante)

    def test_registrar_sucesso_e_checkpoint_grava_execucao_e_estado_na_mesma_sessao(self) -> None:
        session = MagicMock()
        database._schema_initialized = True
        instante = datetime(2026, 9, 9, 15, 0, tzinfo=timezone(timedelta(hours=-3)))

        with patch("app.core.database.orm.main_session", return_value=fake_main_session(session)):
            database.registrar_sucesso_e_checkpoint_pncp(
                escopo="pncp:ufs=CE:modalidades=6",
                executado_em=instante,
                janela_inicio=instante,
                janela_fim=instante,
                quantidade_lida=2,
                quantidade_inserida=1,
                quantidade_atualizada=1,
                parametros={"quando": instante},
                totais={"contratacoes": 1},
            )

        session.add.assert_called_once()
        run = session.add.call_args.args[0]
        self.assertIsInstance(run, PncpIngestionRun)
        self.assertEqual(run.status_execucao, "sucesso")
        self.assertEqual(run.executado_em, datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(run.quantidade_lida, 2)
        self.assertEqual(run.quantidade_com_erro, 0)
        self.assertEqual(run.parametros, {"quando": "2026-09-09 15:00:00-03:00"})
        self.assertEqual(run.totais, {"contratacoes": 1})

        session.merge.assert_called_once()
        state = session.merge.call_args.args[0]
        self.assertIsInstance(state, PncpIngestionState)
        self.assertEqual(state.escopo, "pncp:ufs=CE:modalidades=6")
        self.assertEqual(state.ultima_execucao_sucesso, datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(state.parametros, {"quando": "2026-09-09 15:00:00-03:00"})
        self.assertIsNotNone(state.atualizado_em)


if __name__ == "__main__":
    unittest.main()

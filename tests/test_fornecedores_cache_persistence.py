from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from app.core import database


@contextmanager
def fake_main_session(session: MagicMock):
    yield session


class FornecedoresCachePersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.agora = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
        self.registro = {
            "cnpj": "11444777000161",
            "opencnpj_status": "ok",
            "dados_normalizados": {"cnpj": "11444777000161", "porte_padronizado": "ME"},
            "payload": {"porte_empresa": "ME"},
            "observado_em": self.agora,
            "expira_em": self.agora + timedelta(days=30),
            "ultima_tentativa_em": self.agora,
            "ultima_tentativa_status": "ok",
            "cache_desatualizado": False,
            "atualizado_em": self.agora,
        }

    def test_lista_cache_em_uma_consulta(self) -> None:
        session = MagicMock()
        session.scalars.return_value.all.return_value = [SimpleNamespace(**self.registro)]

        with (
            patch("app.core.database.init_db"),
            patch(
                "app.core.database.orm.main_session",
                return_value=fake_main_session(session),
            ),
        ):
            resultado = database.listar_fornecedores_cache(["11444777000161"])

        self.assertEqual(resultado["11444777000161"]["dados_normalizados"]["porte_padronizado"], "ME")
        session.scalars.assert_called_once()

    def test_salva_cache_em_lote_com_upsert(self) -> None:
        session = MagicMock()

        with (
            patch("app.core.database.init_db"),
            patch(
                "app.core.database.orm.main_session",
                return_value=fake_main_session(session),
            ),
        ):
            quantidade = database.salvar_fornecedores_cache([self.registro])

        self.assertEqual(quantidade, 1)
        session.execute.assert_called_once()
        self.assertEqual(session.execute.call_args.args[1], [self.registro])
        self.assertIn("ON CONFLICT", str(session.execute.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()

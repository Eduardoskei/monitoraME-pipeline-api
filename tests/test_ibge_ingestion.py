from __future__ import annotations

from contextlib import contextmanager
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
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from app.core import database
from app.pipeline.ingestion import ibge


@contextmanager
def fake_main_session(session: MagicMock):
    yield session


class IbgeIngestionTest(unittest.TestCase):
    @patch("app.pipeline.ingestion.ibge.database.salvar_municipios_ibge")
    @patch("app.pipeline.ingestion.ibge.buscar_dados_ibge")
    def test_listar_municipios_persiste_dados_do_ibge(
        self,
        buscar_dados_ibge,
        salvar_municipios_ibge,
    ) -> None:
        municipios = [{"id": 2300101, "nome": "Abaiara"}]
        buscar_dados_ibge.return_value = municipios

        dados = ibge.listar_municipios("CE")

        self.assertEqual(dados, municipios)
        salvar_municipios_ibge.assert_called_once_with(municipios, "CE")

    @patch("app.pipeline.ingestion.ibge.listar_municipios")
    @patch("app.pipeline.ingestion.ibge.database.localizar_municipio_ibge")
    def test_localizar_municipio_consulta_banco_antes_da_api(
        self,
        localizar_municipio_ibge,
        listar_municipios,
    ) -> None:
        municipio = {"id": 2304400, "nome": "Fortaleza"}
        localizar_municipio_ibge.return_value = municipio

        dados = ibge.localizar_municipio(2304400, "CE")

        self.assertEqual(dados, municipio)
        listar_municipios.assert_not_called()

    def test_montar_registro_municipio_ibge_armazena_apenas_campos_basicos(self) -> None:
        registro = database._montar_registro_municipio_ibge(
            {
                "id": 2304400,
                "nome": "Fortaleza",
                "microrregiao": {
                    "mesorregiao": {
                        "UF": {
                            "sigla": "CE",
                        }
                    }
                },
            }
        )

        self.assertEqual(registro["codigo_municipio"], "2304400")
        self.assertEqual(registro["nome"], "Fortaleza")
        self.assertEqual(registro["uf"], "CE")
        self.assertEqual(set(registro), {"codigo_municipio", "nome", "uf"})

    def test_salvar_municipios_tce_persiste_mapeamento_e_nome_em_lote(self) -> None:
        session = MagicMock()
        municipios = [
            {
                "codigo_municipio": "010",
                "codigo_municipio_ibge": "2300754",
                "nome_municipio": "Amontada",
            },
            {"codigo_municipio": "sem-mapeamento"},
        ]

        with (
            patch("app.core.database.init_db"),
            patch(
                "app.core.database.orm.main_session",
                return_value=fake_main_session(session),
            ),
        ):
            quantidade = database.salvar_municipios_tce(municipios, "ce")

        self.assertEqual(quantidade, 1)
        self.assertEqual(session.execute.call_count, 2)
        self.assertEqual(
            session.execute.call_args_list[0].args[1],
            [
                {
                    "codigo_municipio": "2300754",
                    "nome": "Amontada",
                    "uf": "CE",
                }
            ],
        )
        self.assertEqual(
            session.execute.call_args_list[1].args[1],
            [
                {
                    "codigo_municipio_tce": "010",
                    "codigo_municipio_ibge": "2300754",
                }
            ],
        )

    def test_localizar_municipio_tce_faz_join_com_municipio_ibge(self) -> None:
        session = MagicMock()
        session.execute.return_value.mappings.return_value.first.return_value = {
            "codigo_municipio_tce": "010",
            "codigo_municipio_ibge": "2300754",
            "nome": "Amontada",
            "uf": "CE",
        }

        with (
            patch("app.core.database.init_db"),
            patch(
                "app.core.database.orm.main_session",
                return_value=fake_main_session(session),
            ),
        ):
            municipio = database.localizar_municipio_tce(" 010 ")

        self.assertEqual(
            municipio,
            {
                "codigo_municipio_tce": "010",
                "codigo_municipio_ibge": "2300754",
                "nome": "Amontada",
                "uf": "CE",
            },
        )
        self.assertIn("JOIN ibge_municipios", str(session.execute.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()

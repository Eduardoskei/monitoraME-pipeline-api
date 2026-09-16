from __future__ import annotations

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
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_IBGE_PADRAO", "2304400")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")
os.environ.setdefault("MODALIDADE_ID_PADRAO", "6")

from app.core.config import CODIGOS_NATUREZAS_DESPESA_CONSIDERADAS
from app.pipeline.naturezas_despesa import normalizar_codigo_natureza_despesa
from app.pipeline.ingestion import tce


class NaturezasDespesaTest(unittest.TestCase):
    def test_normaliza_codigo_puro_e_classificacao_completa(self) -> None:
        self.assertEqual(normalizar_codigo_natureza_despesa("30"), "30")
        self.assertEqual(normalizar_codigo_natureza_despesa("3.3.90.39"), "39")
        self.assertEqual(normalizar_codigo_natureza_despesa("3.3.90.39.00"), "39")
        self.assertEqual(normalizar_codigo_natureza_despesa("33903900"), "39")

    def test_normaliza_naturezas_completas_com_subelemento(self) -> None:
        exemplos = {
            "33903900": "39",
            "44905100": "51",
            "33903000": "30",
            "44905200": "52",
            "33903200": "32",
            "33903500": "35",
            "33904000": "40",
        }

        for codigo_completo, codigo_elemento in exemplos.items():
            with self.subTest(codigo_completo=codigo_completo):
                self.assertEqual(normalizar_codigo_natureza_despesa(codigo_completo), codigo_elemento)

    def test_nao_extrai_codigo_de_descricao_ou_numero_com_nome(self) -> None:
        self.assertIsNone(normalizar_codigo_natureza_despesa("Material de consumo"))
        self.assertIsNone(normalizar_codigo_natureza_despesa("Material de consumo 30"))
        self.assertIsNone(normalizar_codigo_natureza_despesa("40 - Tecnologia da informacao"))

    def test_todos_os_sete_codigos_configurados_sao_aceitos(self) -> None:
        registros = [
            {"id": indice, "codigo_elemento_despesa": codigo}
            for indice, codigo in enumerate(CODIGOS_NATUREZAS_DESPESA_CONSIDERADAS)
        ]

        self.assertEqual(tce.filtrar_naturezas_despesa(registros), registros)

    def test_descricao_sem_codigo_nao_e_aceita(self) -> None:
        registros = [{"id": 1, "natureza_despesa": "Material de consumo"}]

        self.assertEqual(tce.filtrar_naturezas_despesa(registros), [])

    @patch("app.pipeline.ingestion.tce.buscar_dados_tce")
    def test_listagem_descarta_codigo_fora_do_escopo_antes_de_retornar(
        self,
        buscar_dados_tce,
    ) -> None:
        buscar_dados_tce.return_value = {
            "elements": [
                {"id": 1, "codigo_elemento_despesa": "33903000", "natureza_despesa": "Material de consumo"},
                {"id": 2, "codigo_elemento_despesa": "33903300", "natureza_despesa": "Passagens e despesas"},
            ]
        }

        registros = tce.listar_registros(tce.ENDPOINT_ITENS, {}, filtrar_por_codigo_despesa=True)

        self.assertEqual(
            registros,
            [{"id": 1, "codigo_elemento_despesa": "33903000", "natureza_despesa": "Material de consumo"}],
        )


if __name__ == "__main__":
    unittest.main()

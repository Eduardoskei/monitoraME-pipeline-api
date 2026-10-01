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
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

import pandas as pd

from app.pipeline import analisys


class SerializacaoJsonTest(unittest.TestCase):
    def test_dataframe_para_registros_converte_nulos_e_scalars_do_pandas(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "valor_int": pd.Series([1], dtype="Int64").iloc[0],
                    "valor_nulo": pd.NA,
                    "valor_nan": float("nan"),
                    "data": pd.Timestamp("2025-01-15"),
                }
            ]
        )

        registros = analisys.dataframe_para_registros(df)

        self.assertEqual(
            registros,
            [
                {
                    "valor_int": 1,
                    "valor_nulo": None,
                    "valor_nan": None,
                    "data": "2025-01-15T00:00:00",
                }
            ],
        )


class ConsultarTceContratosTest(unittest.TestCase):
    @patch("app.pipeline.analisys.fornecedores.obter_fornecedores_com_cache")
    @patch("app.pipeline.analisys.tce.buscar_contratados")
    @patch("app.pipeline.analisys.tce.buscar_contratos")
    def test_junta_contratados_enriquece_fornecedor_e_serializa(self, buscar_contratos, buscar_contratados, coletar) -> None:
        buscar_contratos.return_value = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "data_contrato": "2025-01-15",
                "descricao_objeto_contrato": "Servicos de limpeza",
                "valor_total_contrato": "50.000,00",
            }
        ]
        buscar_contratados.return_value = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "numero_documento_negociante": "11.444.777/0001-61",
                "nome_negociante": "Comercio Exemplo LTDA",
            }
        ]
        coletar.return_value = pd.DataFrame(
            [{
                "cnpj": "11444777000161",
                "porte_padronizado": "ME",
                "elegivel_me": True,
                "opencnpj_status": "ok",
            }]
        )

        resposta = analisys.consultar_tce_contratos(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
            enriquecer_fornecedores=True,
            throttle_fornecedores=0,
        )

        self.assertEqual(resposta["fonte"], "TCE-CE")
        self.assertEqual(resposta["totais"], {"contratos": 1})
        registro = resposta["dados"][0]
        self.assertEqual(registro["nome_negociante"], "Comercio Exemplo LTDA")
        self.assertEqual(registro["valor_total_contrato"], 50000.0)
        self.assertEqual(registro["fornecedor_porte_padronizado"], "ME")
        self.assertTrue(registro["fornecedor_elegivel_me"])
        coletar.assert_called_once_with(["11444777000161"], throttle_segundos=0)

    @patch("app.pipeline.analisys.tce.buscar_contratados")
    @patch("app.pipeline.analisys.tce.buscar_contratos")
    def test_ordena_contratos_tce_por_data_ascendente_antes_do_limite(self, buscar_contratos, buscar_contratados) -> None:
        buscar_contratos.return_value = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000001",
                "data_contrato": "2025-01-10",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000002",
                "data_contrato": "2025-02-01",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000003",
                "data_contrato": "2025-01-20",
            },
        ]
        buscar_contratados.return_value = []

        resposta = analisys.consultar_tce_contratos(
            data_inicial="2025-01-01",
            data_final="2025-02-28",
            codigo_municipio="010",
            limite=2,
        )

        self.assertEqual(
            [registro["numero_contrato"] for registro in resposta["dados"]],
            ["2025000001", "2025000003"],
        )


if __name__ == "__main__":
    unittest.main()

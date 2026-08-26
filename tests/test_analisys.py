from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("PNCP_CONSULTA_BASE_URL", "https://pncp.gov.br/api/consulta")
os.environ.setdefault("PNCP_GESTAO_BASE_URL", "https://pncp.gov.br/api/pncp")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://kitana.opencnpj.com")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_IBGE_PADRAO", "2304400")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")
os.environ.setdefault("MODALIDADE_ID_PADRAO", "6")

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


class ConsultarPncpContratacoesTest(unittest.TestCase):
    @patch("app.pipeline.analisys.ibge.listar_municipios")
    @patch("app.pipeline.analisys.pncp.buscar_contratacoes_publicadas")
    def test_retorna_tabelas_limpas_e_municipio_enriquecido(self, buscar_pncp, listar_municipios) -> None:
        buscar_pncp.return_value = [
            {
                "numeroControlePNCP": "11444777000161-1-000001/2025",
                "anoCompra": 2025,
                "sequencialCompra": 1,
                "objetoCompra": "  Material escolar  ",
                "valorTotalEstimado": "1.250,50",
                "unidadeOrgao": {"codigoIbge": 2301000, "ufSigla": "CE"},
            }
        ]
        listar_municipios.return_value = [
            {
                "id": 2301000,
                "nome": "Abaiara",
                "microrregiao": {
                    "mesorregiao": {
                        "UF": {"sigla": "CE"},
                    }
                },
            }
        ]

        resposta = analisys.consultar_pncp_contratacoes(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            max_paginas=1,
        )

        self.assertEqual(resposta["fonte"], "PNCP")
        self.assertEqual(resposta["totais"], {"contratacoes": 1})
        registro = resposta["tabelas"]["contratacoes"][0]
        self.assertEqual(registro["objeto_compra"], "Material escolar")
        self.assertEqual(registro["valor_total_estimado"], 1250.5)
        self.assertEqual(registro["municipio_nome"], "Abaiara")
        self.assertTrue(registro["unidade_orgao_codigo_ibge_uf_confere"])


class ConsultarTceContratosTest(unittest.TestCase):
    @patch("app.pipeline.analisys.fornecedores.coletar_fornecedores_em_lote")
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
        coletar.return_value = [
            {
                "cnpj": "11444777000161",
                "opencnpj": {"cnpj": "11444777000161", "porte": "MICRO EMPRESA"},
                "porte": "MICRO EMPRESA",
                "opencnpj_status": "ok",
            }
        ]

        resposta = analisys.consultar_tce_contratos(
            data_inicial="20250101",
            data_final="20250131",
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


class ConsultarKpiTceMePorMesTest(unittest.TestCase):
    @patch("app.pipeline.analisys.fornecedores.coletar_fornecedores_em_lote")
    @patch("app.pipeline.analisys.tce.buscar_contratados")
    @patch("app.pipeline.analisys.tce.buscar_contratos")
    def test_calcula_kpi_a_partir_do_fluxo_tce(self, buscar_contratos, buscar_contratados, coletar) -> None:
        buscar_contratos.return_value = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "data_contrato": "2025-01-15",
                "valor_total_contrato": "10.000,00",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000456",
                "data_contrato": "2025-01-20",
                "valor_total_contrato": "5.000,00",
            },
        ]
        buscar_contratados.return_value = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "numero_documento_negociante": "11.444.777/0001-61",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000456",
                "numero_documento_negociante": "98.765.432/0001-11",
            },
        ]
        coletar.return_value = [
            {
                "cnpj": "11444777000161",
                "opencnpj": {"porte": "MICRO EMPRESA"},
                "porte": "MICRO EMPRESA",
                "opencnpj_status": "ok",
            },
            {
                "cnpj": "98765432000111",
                "opencnpj": {"porte": "EMPRESA DE PEQUENO PORTE"},
                "porte": "EMPRESA DE PEQUENO PORTE",
                "opencnpj_status": "ok",
            },
        ]

        resposta = analisys.consultar_kpi_tce_me_por_mes(
            data_inicial="20250101",
            data_final="20250131",
            codigo_municipio="010",
            throttle_fornecedores=0,
        )

        self.assertEqual(resposta["kpi"], "participacao_me_por_mes")
        self.assertEqual(resposta["totais"], {"contratos": 2, "periodos": 1})
        self.assertEqual(resposta["dados"][0]["ano_mes"], "2025-01")
        self.assertEqual(resposta["dados"][0]["total_compras"], 15000.0)
        self.assertEqual(resposta["dados"][0]["valor_me"], 10000.0)


if __name__ == "__main__":
    unittest.main()

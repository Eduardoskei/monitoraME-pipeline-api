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
                "codigoElementoDespesa": "33903000",
                "valorTotalEstimado": "1.250,50",
                "unidadeOrgao": {"codigoIbge": 2301000, "ufSigla": "CE"},
            },
            {
                "numeroControlePNCP": "11444777000161-1-000002/2025",
                "anoCompra": 2025,
                "sequencialCompra": 2,
                "objetoCompra": "Material de consumo sem codigo",
                "valorTotalEstimado": "900,00",
                "unidadeOrgao": {"codigoIbge": 2301000, "ufSigla": "CE"},
            },
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
                "opencnpj": {"cnpj": "11444777000161", "porte_empresa": "MICRO EMPRESA"},
                "porte": "MICRO EMPRESA",
                "opencnpj_status": "ok",
            }
        ]

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


class MontarBaseAnaliticaTceTest(unittest.TestCase):
    @patch("app.pipeline.analisys.fornecedores.coletar_fornecedores_em_lote")
    @patch("app.pipeline.analisys.tce.buscar_contratados")
    @patch("app.pipeline.analisys.tce.buscar_contratos")
    def test_monta_base_canonica_para_indicadores(
        self,
        buscar_contratos,
        buscar_contratados,
        coletar,
    ) -> None:
        buscar_contratos.return_value = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "data_contrato": "2025-01-15",
                "codigo_elemento_despesa": "33903000",
                "valor_total_contrato": "10.000,00",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000456",
                "data_contrato": "2025-02-01",
                "codigo_elemento_despesa": "33903900",
                "valor_total_contrato": "20.000,00",
            },
        ]
        buscar_contratados.return_value = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "numero_documento_negociante": "11.444.777/0001-61",
                "nome_negociante": "Comercio Local LTDA",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000456",
                "numero_documento_negociante": "98.765.432/0001-11",
                "nome_negociante": "Fornecedor Fortaleza SA",
            },
        ]
        coletar.return_value = [
            {
                "cnpj": "11444777000161",
                "razao_social": "Comercio Local LTDA",
                "porte": "MICRO EMPRESA",
                "opencnpj": {
                    "porte_empresa": "MICRO EMPRESA",
                    "municipio": "AMONTADA",
                    "uf": "CE",
                    "cnae_principal": "4711302",
                },
                "opencnpj_status": "ok",
            },
            {
                "cnpj": "98765432000111",
                "razao_social": "Fornecedor Fortaleza SA",
                "porte": "EMPRESA DE PEQUENO PORTE",
                "opencnpj": {
                    "porte_empresa": "EMPRESA DE PEQUENO PORTE",
                    "municipio": "FORTALEZA",
                    "uf": "CE",
                    "cnae_principal": "6201501",
                },
                "opencnpj_status": "ok",
            },
        ]

        base, metadados = analisys.montar_base_analitica_tce(
            data_inicial="2025-01-01",
            data_final="2025-02-28",
            codigo_municipio="010",
            municipio_comprador="Amontada",
            throttle_fornecedores=0,
        )

        self.assertEqual(metadados["base_analitica"]["nome"], "tce_contratos")
        self.assertEqual(metadados["base_analitica"]["municipio_comprador"], "Amontada")
        self.assertEqual(len(base), 2)
        self.assertEqual(
            list(base.columns),
            metadados["base_analitica"]["colunas"],
        )

        primeiro = base[base["numero_contrato"] == "2025000123"].iloc[0]
        self.assertEqual(primeiro["fonte"], "TCE-CE")
        self.assertEqual(primeiro["base_calculo"], "contratos_tce")
        self.assertEqual(primeiro["codigo_municipio_tce"], "010")
        self.assertEqual(primeiro["municipio_comprador"], "Amontada")
        self.assertEqual(primeiro["ano"], "2025")
        self.assertEqual(primeiro["ano_mes"], "2025-01")
        self.assertEqual(primeiro["natureza_despesa_codigo"], "30")
        self.assertEqual(primeiro["natureza_despesa"], "Material de consumo")
        self.assertEqual(primeiro["valor"], 10000.0)
        self.assertEqual(primeiro["cnpj_fornecedor"], "11444777000161")
        self.assertEqual(primeiro["porte_fornecedor"], "ME")
        self.assertTrue(primeiro["fornecedor_e_me"])
        self.assertTrue(primeiro["fornecedor_e_mpe"])
        self.assertEqual(primeiro["municipio_sede_fornecedor"], "AMONTADA")
        self.assertEqual(primeiro["uf_sede_fornecedor"], "CE")
        self.assertEqual(primeiro["cnae_principal_fornecedor"], "4711302")
        self.assertEqual(primeiro["origem_geografica"], "Sediado no município comprador")

        segundo = base[base["numero_contrato"] == "2025000456"].iloc[0]
        self.assertEqual(segundo["natureza_despesa_codigo"], "39")
        self.assertEqual(segundo["porte_fornecedor"], "EPP")
        self.assertFalse(segundo["fornecedor_e_me"])
        self.assertTrue(segundo["fornecedor_e_mpe"])
        self.assertEqual(segundo["origem_geografica"], "Outro município do Ceará")


class ConsultarTceIndicadoresAnaliticosTest(unittest.TestCase):
    @patch("app.pipeline.analisys.tce.buscar_municipios")
    @patch("app.pipeline.analisys.fornecedores.coletar_fornecedores_em_lote")
    @patch("app.pipeline.analisys.tce.buscar_contratados")
    @patch("app.pipeline.analisys.tce.buscar_contratos")
    def test_calcula_indicadores_principais_a_partir_da_base_analitica(
        self,
        buscar_contratos,
        buscar_contratados,
        coletar,
        buscar_municipios,
    ) -> None:
        buscar_municipios.return_value = [
            {"codigo_municipio": "010", "nome_municipio": "Amontada"},
            {"codigo_municipio": "020", "nome_municipio": "Aquiraz"},
        ]
        buscar_contratos.return_value = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "data_contrato": "2025-01-15",
                "codigo_elemento_despesa": "33903000",
                "valor_total_contrato": "10.000,00",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000456",
                "data_contrato": "2025-01-20",
                "codigo_elemento_despesa": "33903900",
                "valor_total_contrato": "20.000,00",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000789",
                "data_contrato": "2025-02-05",
                "codigo_elemento_despesa": "33903000",
                "valor_total_contrato": "5.000,00",
            },
        ]
        buscar_contratados.return_value = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "numero_documento_negociante": "11.444.777/0001-61",
                "nome_negociante": "Comercio Local LTDA",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000456",
                "numero_documento_negociante": "98.765.432/0001-11",
                "nome_negociante": "Fornecedor Fortaleza SA",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000789",
                "numero_documento_negociante": "55.555.555/0001-55",
                "nome_negociante": "Fornecedor Nacional SA",
            },
        ]
        coletar.return_value = [
            {
                "cnpj": "11444777000161",
                "razao_social": "Comercio Local LTDA",
                "porte": "MICRO EMPRESA",
                "opencnpj": {
                    "porte_empresa": "MICRO EMPRESA",
                    "municipio": "AMONTADA",
                    "uf": "CE",
                },
                "opencnpj_status": "ok",
            },
            {
                "cnpj": "98765432000111",
                "razao_social": "Fornecedor Fortaleza SA",
                "porte": "EMPRESA DE PEQUENO PORTE",
                "opencnpj": {
                    "porte_empresa": "EMPRESA DE PEQUENO PORTE",
                    "municipio": "FORTALEZA",
                    "uf": "CE",
                },
                "opencnpj_status": "ok",
            },
            {
                "cnpj": "55555555000155",
                "razao_social": "Fornecedor Nacional SA",
                "porte": "DEMAIS",
                "opencnpj": {
                    "porte_empresa": "DEMAIS",
                    "municipio": "SAO PAULO",
                    "uf": "SP",
                },
                "opencnpj_status": "ok",
            },
        ]

        resposta = analisys.consultar_tce_indicadores_analiticos(
            data_inicial="2025-01-01",
            data_final="2025-02-28",
            codigo_municipio="010",
            throttle_fornecedores=0,
            limite_ranking=2,
        )

        self.assertEqual(resposta["kpi"], "indicadores_analiticos_principais")
        self.assertEqual(resposta["base_analitica"]["municipio_comprador"], "Amontada")
        self.assertEqual(resposta["indicadores_analiticos"]["escopo"], "principais")
        self.assertEqual(resposta["totais"]["resumo_geral"], 1)
        self.assertEqual(resposta["totais"]["serie_mensal"], 2)
        self.assertEqual(resposta["totais"]["ranking_fornecedores"], 2)

        resumo = resposta["indicadores"]["resumo_geral"][0]
        self.assertEqual(resumo["valor_total"], 35000.0)
        self.assertEqual(resumo["valor_me"], 10000.0)
        self.assertEqual(resumo["valor_mpe"], 30000.0)
        self.assertEqual(resumo["valor_fornecedor_local"], 10000.0)

        ranking = resposta["indicadores"]["ranking_fornecedores"]
        self.assertEqual(ranking[0]["cnpj_fornecedor"], "98765432000111")
        self.assertEqual(ranking[0]["valor_total"], 20000.0)


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
                "opencnpj": {"porte_empresa": "MICRO EMPRESA"},
                "porte": "MICRO EMPRESA",
                "opencnpj_status": "ok",
            },
            {
                "cnpj": "98765432000111",
                "opencnpj": {"porte_empresa": "EMPRESA DE PEQUENO PORTE"},
                "porte": "EMPRESA DE PEQUENO PORTE",
                "opencnpj_status": "ok",
            },
        ]

        resposta = analisys.consultar_kpi_tce_me_por_mes(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
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

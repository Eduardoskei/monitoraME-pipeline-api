from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

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

from app.pipeline import kpis


class ExtrairAnoMesTest(unittest.TestCase):
    def test_extrai_de_data_e_de_datetime_com_z(self) -> None:
        serie = pd.Series(["2025-01-15", "2025-02-10T09:00:00Z", "2025-03-01T09:00:00", None, "nao_informado"])

        resultado = kpis.extrair_ano_mes(serie)

        self.assertEqual(
            resultado.tolist()[:3],
            ["2025-01", "2025-02", "2025-03"],
        )
        self.assertTrue(resultado.isna().tolist()[3:] == [True, True])


class CalcularIndicadoresAnaliticosPrincipaisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = pd.DataFrame(
            [
                {
                    "ano_mes": "2025-01",
                    "valor": 10000.0,
                    "cnpj_fornecedor": "11444777000161",
                    "nome_fornecedor": "Comercio Local LTDA",
                    "porte_fornecedor": "ME",
                    "fornecedor_e_me": True,
                    "fornecedor_e_mpe": True,
                    "natureza_despesa_codigo": "30",
                    "natureza_despesa": "Material de consumo",
                    "origem_geografica": "Sediado no município comprador",
                    "municipio_sede_fornecedor": "AMONTADA",
                    "uf_sede_fornecedor": "CE",
                },
                {
                    "ano_mes": "2025-01",
                    "valor": 20000.0,
                    "cnpj_fornecedor": "98765432000111",
                    "nome_fornecedor": "Fornecedor EPP SA",
                    "porte_fornecedor": "EPP",
                    "fornecedor_e_me": False,
                    "fornecedor_e_mpe": True,
                    "natureza_despesa_codigo": "39",
                    "natureza_despesa": "Outros servicos de terceiros - pessoa juridica",
                    "origem_geografica": "Outro município do Ceará",
                    "municipio_sede_fornecedor": "FORTALEZA",
                    "uf_sede_fornecedor": "CE",
                },
                {
                    "ano_mes": "2025-02",
                    "valor": 5000.0,
                    "cnpj_fornecedor": "55555555000155",
                    "nome_fornecedor": "Fornecedor Nacional SA",
                    "porte_fornecedor": "DEMAIS",
                    "fornecedor_e_me": False,
                    "fornecedor_e_mpe": False,
                    "natureza_despesa_codigo": "30",
                    "natureza_despesa": "Material de consumo",
                    "origem_geografica": "Fora do estado",
                    "municipio_sede_fornecedor": "SAO PAULO",
                    "uf_sede_fornecedor": "SP",
                },
                {
                    "ano_mes": "2025-02",
                    "valor": None,
                    "cnpj_fornecedor": None,
                    "nome_fornecedor": "Fornecedor sem valor",
                    "porte_fornecedor": "NAO_IDENTIFICADO",
                    "fornecedor_e_me": False,
                    "fornecedor_e_mpe": False,
                    "natureza_despesa_codigo": "30",
                    "natureza_despesa": "Material de consumo",
                    "origem_geografica": "Nao identificada",
                    "municipio_sede_fornecedor": None,
                    "uf_sede_fornecedor": None,
                },
            ]
        )

    def test_calcula_resumo_series_distribuicoes_e_ranking(self) -> None:
        indicadores = kpis.calcular_indicadores_analiticos_principais(self.base, limite_ranking=2)

        self.assertEqual(set(indicadores), set(kpis.INDICADORES_ANALITICOS_PRINCIPAIS))

        resumo = indicadores["resumo_geral"].iloc[0]
        self.assertEqual(resumo["total_registros"], 4)
        self.assertEqual(resumo["total_fornecedores"], 3)
        self.assertEqual(resumo["registros_sem_valor"], 1)
        self.assertEqual(resumo["valor_total"], 35000.0)
        self.assertEqual(resumo["valor_me"], 10000.0)
        self.assertAlmostEqual(resumo["percentual_me"], 10000.0 / 35000.0)
        self.assertEqual(resumo["valor_mpe"], 30000.0)
        self.assertAlmostEqual(resumo["percentual_mpe"], 30000.0 / 35000.0)
        self.assertEqual(resumo["valor_fornecedor_local"], 10000.0)

        serie = indicadores["serie_mensal"].set_index("ano_mes")
        self.assertEqual(serie.loc["2025-01", "valor_total"], 30000.0)
        self.assertEqual(serie.loc["2025-01", "valor_mpe"], 30000.0)
        self.assertEqual(serie.loc["2025-02", "valor_total"], 5000.0)

        naturezas = indicadores["por_natureza_despesa"].set_index("natureza_despesa_codigo")
        self.assertEqual(naturezas.loc["30", "registros"], 3)
        self.assertEqual(naturezas.loc["30", "valor_total"], 15000.0)

        ranking = indicadores["ranking_fornecedores"]
        self.assertEqual(len(ranking), 2)
        self.assertEqual(ranking.iloc[0]["cnpj_fornecedor"], "98765432000111")
        self.assertEqual(ranking.iloc[0]["valor_total"], 20000.0)

    def test_recusa_base_sem_colunas_canonicas(self) -> None:
        with self.assertRaisesRegex(kpis.DadosInsuficientesKPI, "base analitica"):
            kpis.calcular_indicadores_analiticos_principais(pd.DataFrame([{"valor": 100.0}]))

    def test_recusa_limite_ranking_invalido(self) -> None:
        with self.assertRaisesRegex(ValueError, "limite_ranking"):
            kpis.calcular_indicadores_analiticos_principais(self.base, limite_ranking=0)


class CalcularParticipacaoMeLocalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.licitacoes = pd.DataFrame([
            {"id": "L1", "municipio_comprador": "São Gonçalo do Amarante", "secretaria": "Saúde", "data": "2025-01-10"},
            {"id": "L2", "municipio_comprador": "São Gonçalo do Amarante", "secretaria": "Educação", "data": "2025-02-10"},
            {"id": "L3", "municipio_comprador": "São Gonçalo do Amarante", "secretaria": "Saúde", "data": "2026-01-10"},
        ])

    def test_conta_licitacao_uma_vez_e_exclui_rotulos_nao_me(self) -> None:
        participantes = pd.DataFrame([
            {"licitacao": "L1", "cnpj": "11.444.777/0001-61", "porte": "ME", "municipio_empresa": "SAO GONCALO DO AMARANTE"},
            {"licitacao": "L1", "cnpj": "12.345.678/0001-00", "porte": "MICRO EMPRESA", "municipio_empresa": "São Gonçalo do Amarante"},
            {"licitacao": "L2", "cnpj": "22.222.222/0001-22", "porte": "EPP", "municipio_empresa": "São Gonçalo do Amarante"},
            {"licitacao": "L2", "cnpj": "33.333.333/0001-33", "porte": "ME/EPP", "municipio_empresa": "São Gonçalo do Amarante"},
            {"licitacao": "L3", "cnpj": "44.444.444/0001-44", "porte": "ME", "municipio_empresa": "Fortaleza"},
        ])

        resultado = kpis.calcular_participacao_me_local(
            self.licitacoes,
            participantes,
            coluna_licitacao="id",
            coluna_licitacao_participante="licitacao",
            coluna_porte="porte",
            coluna_municipio_empresa="municipio_empresa",
            coluna_municipio_comprador="municipio_comprador",
            coluna_cnpj="cnpj",
            coluna_secretaria="secretaria",
            coluna_data="data",
        )
        resumo = resultado["resumo_geral"].iloc[0]
        self.assertEqual(resumo["total_licitacoes"], 3)
        self.assertEqual(resumo["licitacoes_com_me_local"], 1)
        self.assertEqual(resumo["licitacoes_sem_me_local"], 2)
        self.assertAlmostEqual(resumo["percentual_me_local"], 100 / 3)
        self.assertEqual(resumo["licitacoes_com_me_externa"], 1)
        self.assertEqual(len(resultado["por_secretaria"]), 2)
        self.assertEqual(len(resultado["historico"]), 2)

    def test_recusa_calcular_sem_proponentes(self) -> None:
        with self.assertRaisesRegex(kpis.DadosInsuficientesKPI, "vencedores nao substituem"):
            kpis.calcular_participacao_me_local(
                self.licitacoes,
                pd.DataFrame(),
                coluna_licitacao="id",
                coluna_licitacao_participante="licitacao",
                coluna_porte="porte",
                coluna_municipio_empresa="municipio_empresa",
                coluna_municipio_comprador="municipio_comprador",
            )


if __name__ == "__main__":
    unittest.main()

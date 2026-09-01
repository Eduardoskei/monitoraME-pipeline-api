from __future__ import annotations

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

import pandas as pd

from app.pipeline import cleaning, kpis, merge
from app.pipeline.ingestion import fornecedores, tce


def _fake_response(payload, status_code: int = 200) -> MagicMock:
    resposta = MagicMock()
    resposta.status_code = status_code
    resposta.json.return_value = payload
    resposta.raise_for_status.return_value = None
    return resposta


class ExtrairAnoMesTest(unittest.TestCase):
    def test_extrai_de_data_e_de_datetime_com_z(self) -> None:
        serie = pd.Series(["2025-01-15", "2025-02-10T09:00:00Z", "2025-03-01T09:00:00", None, "nao_informado"])

        resultado = kpis.extrair_ano_mes(serie)

        self.assertEqual(
            resultado.tolist()[:3],
            ["2025-01", "2025-02", "2025-03"],
        )
        self.assertTrue(resultado.isna().tolist()[3:] == [True, True])


class CalcularParticipacaoMeTest(unittest.TestCase):
    def test_soma_por_grupo_e_calcula_percentual(self) -> None:
        df = pd.DataFrame(
            [
                {"ano_mes": "2025-01", "valor": 50000.0, "fornecedor_elegivel_me": True},
                {"ano_mes": "2025-01", "valor": 20000.0, "fornecedor_elegivel_me": False},
                {"ano_mes": "2025-02", "valor": 10000.0, "fornecedor_elegivel_me": True},
                # fornecedor nao localizado (None) -> entra no total, nao entra no valor_me
                {"ano_mes": "2025-02", "valor": 5000.0, "fornecedor_elegivel_me": None},
            ]
        )

        resultado = kpis.calcular_participacao_me(
            df, colunas_agrupamento=["ano_mes"], coluna_valor="valor"
        )
        por_mes = resultado.set_index("ano_mes")

        self.assertEqual(por_mes.loc["2025-01", "total_compras"], 70000.0)
        self.assertEqual(por_mes.loc["2025-01", "valor_me"], 50000.0)
        self.assertAlmostEqual(por_mes.loc["2025-01", "percentual_me"], 50000.0 / 70000.0)

        self.assertEqual(por_mes.loc["2025-02", "total_compras"], 15000.0)
        self.assertEqual(por_mes.loc["2025-02", "valor_me"], 10000.0)  # a linha None nao conta
        self.assertAlmostEqual(por_mes.loc["2025-02", "percentual_me"], 10000.0 / 15000.0)

    def test_grupo_com_total_zero_nao_gera_divisao_por_zero(self) -> None:
        df = pd.DataFrame(
            [
                {"ano_mes": "2025-01", "valor": 0.0, "fornecedor_elegivel_me": False},
            ]
        )

        resultado = kpis.calcular_participacao_me(
            df, colunas_agrupamento=["ano_mes"], coluna_valor="valor"
        )

        self.assertTrue(pd.isna(resultado.iloc[0]["percentual_me"]))

    def test_aceita_multiplas_colunas_de_agrupamento(self) -> None:
        df = pd.DataFrame(
            [
                {"ano_mes": "2025-01", "municipio": "Amontada", "valor": 100.0, "fornecedor_elegivel_me": True},
                {"ano_mes": "2025-01", "municipio": "Abaiara", "valor": 200.0, "fornecedor_elegivel_me": False},
            ]
        )

        resultado = kpis.calcular_participacao_me(
            df, colunas_agrupamento=["ano_mes", "municipio"], coluna_valor="valor"
        )

        self.assertEqual(len(resultado), 2)


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


class ParticipacaoMePorMesEndToEndTest(unittest.TestCase):
    """Usa a ingestao + limpeza + merge reais do TCE, so a chamada HTTP e mockada."""

    def test_calcula_a_partir_da_base_tce_ja_enriquecida(self) -> None:
        registros_contratos = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "data_contrato": "2025-01-15",
                "descricao_objeto_contrato": "Prestacao de servicos de limpeza predial",
                "valor_total_contrato": "50.000,00",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000456",
                "data_contrato": "2025-02-01",
                "descricao_objeto_contrato": "Fornecimento de material de expediente",
                "valor_total_contrato": "20.000,00",
            },
        ]
        registros_contratados = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "numero_documento_negociante": "11.444.777/0001-61",
                "nome_negociante": "Comércio Exemplo LTDA",
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000456",
                "numero_documento_negociante": "98.765.432/0001-11",
                "nome_negociante": "Fornecedor EPP SA",
            },
        ]

        with patch("app.pipeline.ingestion.tce.requests.get") as mock_get:
            mock_get.return_value = _fake_response({"elements": registros_contratos})
            df_contratos = cleaning.limpar_tce(tce.buscar_contratos("20250101", "20250301", codigo_municipio="010"))

        with patch("app.pipeline.ingestion.tce.requests.get") as mock_get:
            mock_get.return_value = _fake_response({"elements": registros_contratados})
            df_contratados = cleaning.limpar_tce(
                tce.buscar_contratados("20250101", "20250301", codigo_municipio="010")
            )

        with patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj") as mock_opencnpj:
            # ME entra no KPI; EPP existe na fonte, mas nao entra neste projeto.
            mock_opencnpj.side_effect = [
                {"cnpj": "11444777000161", "porte": "MICRO EMPRESA"},
                {"cnpj": "98765432000111", "porte": "EMPRESA DE PEQUENO PORTE"},
            ]
            brutos = fornecedores.coletar_fornecedores_em_lote(
                ["11.444.777/0001-61", "98.765.432/0001-11"], throttle_segundos=0
            )
        fornecedores_df = cleaning.limpar_fornecedores(brutos)

        base_tce = merge.montar_base_tce(df_contratos, df_contratados, fornecedores_df=fornecedores_df)

        resultado = kpis.calcular_participacao_me_por_mes(
            base_tce, coluna_data="data_contrato", coluna_valor="valor_total_contrato"
        )
        por_mes = resultado.set_index("ano_mes")

        self.assertEqual(por_mes.loc["2025-01", "total_compras"], 50000.0)
        self.assertEqual(por_mes.loc["2025-01", "valor_me"], 50000.0)  # ME -> 100%
        self.assertEqual(por_mes.loc["2025-01", "percentual_me"], 1.0)

        self.assertEqual(por_mes.loc["2025-02", "total_compras"], 20000.0)
        self.assertEqual(por_mes.loc["2025-02", "valor_me"], 0.0)  # EPP -> 0%
        self.assertEqual(por_mes.loc["2025-02", "percentual_me"], 0.0)


if __name__ == "__main__":
    unittest.main()

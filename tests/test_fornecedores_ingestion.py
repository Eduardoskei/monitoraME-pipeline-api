from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
import requests
from unittest.mock import patch

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

from app.pipeline.ingestion import fornecedores


class FornecedoresIngestionTest(unittest.TestCase):
    def test_somente_digitos_limpa_cnpj_para_chamadas(self) -> None:
        self.assertEqual(fornecedores.somente_digitos("12.345.678/0001-99"), "12345678000199")

    def test_normalizar_porte_me_identifica_microempresa(self) -> None:
        self.assertEqual(fornecedores.normalizar_porte_me("ME"), "ME")
        self.assertEqual(fornecedores.normalizar_porte_me("MICRO EMPRESA"), "ME")
        self.assertEqual(fornecedores.normalizar_porte_me("micro-empresa"), "ME")
        self.assertIsNone(fornecedores.normalizar_porte_me("EPP"))

    @patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj")
    def test_coletar_fornecedor_retorna_payload_bruto(self, buscar_opencnpj) -> None:
        buscar_opencnpj.return_value = {"porte": "ME", "razao_social": "EMPRESA TESTE LTDA"}

        dados = fornecedores.coletar_fornecedor("12.345.678/0001-99")

        self.assertEqual(dados["cnpj"], "12345678000199")
        self.assertEqual(dados["opencnpj"], {"porte": "ME", "razao_social": "EMPRESA TESTE LTDA"})
        self.assertEqual(dados["razao_social"], "EMPRESA TESTE LTDA")
        self.assertEqual(dados["porte"], "ME")
        self.assertEqual(dados["porte_fonte"], "opencnpj")
        self.assertEqual(dados["opencnpj_status"], "ok")

    @patch("app.pipeline.ingestion.fornecedores.time.sleep")
    @patch("app.pipeline.ingestion.fornecedores.requests.get")
    def test_falha_da_fonte_nao_e_mascarada_como_cnpj_sem_dados(self, get, _sleep) -> None:
        get.side_effect = requests.ReadTimeout("fonte demorou")

        with self.assertRaises(fornecedores.FonteCadastralIndisponivelError):
            fornecedores._get_json("https://fonte.test/cnpj", max_retries=1)

    @patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj")
    def test_coleta_marca_opencnpj_indisponivel(self, opencnpj) -> None:
        opencnpj.side_effect = fornecedores.FonteCadastralIndisponivelError("timeout")

        dados = fornecedores.coletar_fornecedor("11.444.777/0001-61")

        self.assertEqual(dados["opencnpj_status"], "indisponivel")
        self.assertEqual(dados["opencnpj"], {})
        self.assertIsNone(dados["porte"])
        self.assertIsNone(dados["porte_fonte"])

    @patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj")
    @patch("app.pipeline.ingestion.fornecedores.database.localizar_fornecedor_me")
    def test_validar_fornecedor_me_usa_banco_sem_chamar_apis(
        self,
        localizar_fornecedor_me,
        buscar_opencnpj,
    ) -> None:
        fornecedor = {
            "cnpj": "12345678000199",
            "razao_social": "EMPRESA TESTE LTDA",
            "porte": "ME",
        }
        localizar_fornecedor_me.return_value = fornecedor

        dados = fornecedores.validar_fornecedor_me("12.345.678/0001-99")

        self.assertEqual(dados, fornecedor)
        buscar_opencnpj.assert_not_called()

    @patch("app.pipeline.ingestion.fornecedores.database.salvar_fornecedor_me")
    @patch("app.pipeline.ingestion.fornecedores.database.localizar_fornecedor_me")
    @patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj")
    def test_validar_fornecedor_me_salva_apenas_quando_porte_e_me(
        self,
        buscar_opencnpj,
        localizar_fornecedor_me,
        salvar_fornecedor_me,
    ) -> None:
        localizar_fornecedor_me.return_value = None
        buscar_opencnpj.return_value = {
            "cnpj": "12345678000199",
            "razao_social": "EMPRESA TESTE LTDA",
            "porte": "MICRO EMPRESA",
        }

        dados = fornecedores.validar_fornecedor_me("12.345.678/0001-99")

        self.assertEqual(
            dados,
            {
                "cnpj": "12345678000199",
                "razao_social": "EMPRESA TESTE LTDA",
                "porte": "ME",
            },
        )
        salvar_fornecedor_me.assert_called_once_with("12345678000199", "EMPRESA TESTE LTDA", "ME")

    @patch("app.pipeline.ingestion.fornecedores.database.salvar_fornecedor_me")
    @patch("app.pipeline.ingestion.fornecedores.database.localizar_fornecedor_me")
    @patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj")
    def test_validar_fornecedor_me_nao_salva_empresa_que_nao_e_me(
        self,
        buscar_opencnpj,
        localizar_fornecedor_me,
        salvar_fornecedor_me,
    ) -> None:
        localizar_fornecedor_me.return_value = None
        buscar_opencnpj.return_value = {
            "razao_social": "EMPRESA MEDIA LTDA",
            "porte": "DEMAIS",
        }

        dados = fornecedores.validar_fornecedor_me("12.345.678/0001-99")

        self.assertIsNone(dados)
        salvar_fornecedor_me.assert_not_called()

    @patch("app.pipeline.ingestion.fornecedores.time.sleep")
    @patch("app.pipeline.ingestion.fornecedores.coletar_fornecedor")
    def test_coletar_fornecedores_em_lote_ignora_duplicatas_e_pausa_entre_chamadas(
        self, mock_coletar, mock_sleep
    ) -> None:
        mock_coletar.side_effect = lambda cnpj: {"cnpj": cnpj}

        resultados = fornecedores.coletar_fornecedores_em_lote(
            # os 2 primeiros sao o mesmo CNPJ em formatos diferentes -> so 1 chamada
            ["11444777000161", "11.444.777/0001-61", "98765432000199"],
            throttle_segundos=0.1,
        )

        self.assertEqual(mock_coletar.call_count, 2)
        self.assertEqual({r["cnpj"] for r in resultados}, {"11444777000161", "98765432000199"})
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_called_with(0.1)

    @patch("app.pipeline.ingestion.fornecedores.time.sleep")
    @patch("app.pipeline.ingestion.fornecedores.coletar_fornecedor")
    def test_coletar_fornecedores_em_lote_sem_throttle_nao_dorme(self, mock_coletar, mock_sleep) -> None:
        mock_coletar.side_effect = lambda cnpj: {"cnpj": cnpj}

        fornecedores.coletar_fornecedores_em_lote(["11444777000161"], throttle_segundos=0)

        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()

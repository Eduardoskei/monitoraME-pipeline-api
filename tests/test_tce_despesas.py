from __future__ import annotations

from decimal import Decimal
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

from app.pipeline.cleaners import tce_despesas


class ContratoTceDespesasTest(unittest.TestCase):
    def setUp(self) -> None:
        self.empenho = {
            "codigo_municipio": "010",
            "exercicio_orcamento": 202500,
            "codigo_orgao": "17",
            "codigo_unidade_orcamentaria": "01  ",
            "data_emissao_empenho": "2025-01-31T00:00:00",
            "numero_empenho": "31010002",
            "data_referencia_doc": 202501,
            "codigo_elemento_despesa": "44905200",
            "valor_empenhado": Decimal("11115.0000"),
            "numero_documento_negociante": "10496308000123",
            "codigo_tipo_negociante": "1",
            "nome_negociante": "FRANCISCO FÁBIO DA SILVA BARBOSA - ME",
            "municipio_negociante": "Quixeramobim",
            "codigo_uf": "CE",
            "estado_empenho": "OR",
            "numero_nota_anulacao": "",
            "numero_empenho_substituto": "",
            "numero_contrato": "006/2025",
            "numero_licitacao": "DE 001/2025",
        }
        self.anulacao = {
            "codigo_municipio": "010",
            "exercicio_orcamento": 202500,
            "codigo_orgao": "17",
            "codigo_unidade_orcamentaria": "01  ",
            "data_emissao_empenho": "2025-01-31T00:00:00",
            "numero_nota_empenho": "31010002",
            "numero_nota_anulacao": "10010001",
            "data_referencia_doc": 202501,
            "data_anulacao": "2025-02-10T00:00:00",
            "modalidade_anulacao": "T",
            "descricao_anulacao": "ANULAÇÃO DO SALDO DO EMPENHO.",
            "valor_anulacao": Decimal("59.50"),
        }

    def test_mapeia_empenho_com_data_civil_centavos_e_chave_natural(self) -> None:
        resultado = tce_despesas.mapear_nota_empenho(self.empenho)

        self.assertEqual(resultado["competencia"], "2025-01")
        self.assertEqual(resultado["exercicio"], 2025)
        self.assertEqual(resultado["codigo_unidade_orcamentaria"], "01")
        self.assertEqual(resultado["data_empenho"], "2025-01-31")
        self.assertEqual(resultado["valor_empenhado_centavos"], 1_111_500)
        self.assertEqual(resultado["codigo_elemento_despesa"], "52")
        self.assertTrue(resultado["natureza_considerada"])
        self.assertEqual(resultado["cnpj_fornecedor"], "10496308000123")
        self.assertEqual(
            resultado["chave_empenho"],
            "010|202500|17|01|2025-01-31|31010002",
        )
        self.assertEqual(resultado["payload_bruto"], self.empenho)

    def test_mapeia_anulacao_para_a_mesma_chave_do_empenho(self) -> None:
        empenho = tce_despesas.mapear_nota_empenho(self.empenho)
        anulacao = tce_despesas.mapear_nota_anulacao_empenho(self.anulacao)

        self.assertEqual(anulacao["chave_empenho"], empenho["chave_empenho"])
        self.assertEqual(anulacao["data_anulacao"], "2025-02-10")
        self.assertEqual(anulacao["valor_anulacao_centavos"], 5950)
        self.assertEqual(
            anulacao["chave_anulacao"],
            "010|202500|17|01|2025-01-31|31010002|2025-02-10|10010001",
        )

    def test_valor_para_centavos_arredonda_half_up_sem_float(self) -> None:
        self.assertEqual(tce_despesas.valor_para_centavos("1,005", "valor"), 101)
        self.assertEqual(tce_despesas.valor_para_centavos(11115, "valor"), 1_111_500)

    def test_documento_invalido_permanece_no_payload_sem_virar_cnpj(self) -> None:
        self.empenho["numero_documento_negociante"] = "123"

        resultado = tce_despesas.mapear_nota_empenho(self.empenho)

        self.assertEqual(resultado["documento_fornecedor"], "123")
        self.assertIsNone(resultado["cnpj_fornecedor"])

    def test_rejeita_registro_sem_chave_natural(self) -> None:
        self.empenho["numero_empenho"] = ""

        with self.assertRaisesRegex(tce_despesas.ContratoDespesaTceInvalido, "numero_empenho"):
            tce_despesas.mapear_nota_empenho(self.empenho)

    def test_rejeita_competencia_de_outro_exercicio(self) -> None:
        self.empenho["data_referencia_doc"] = 202401

        with self.assertRaisesRegex(tce_despesas.ContratoDespesaTceInvalido, "exercicio_orcamento"):
            tce_despesas.mapear_nota_empenho(self.empenho)

    def test_rejeita_codigo_de_natureza_fora_do_contrato_oficial(self) -> None:
        self.empenho["codigo_elemento_despesa"] = "52"

        with self.assertRaisesRegex(tce_despesas.ContratoDespesaTceInvalido, "oito dígitos"):
            tce_despesas.mapear_nota_empenho(self.empenho)


if __name__ == "__main__":
    unittest.main()

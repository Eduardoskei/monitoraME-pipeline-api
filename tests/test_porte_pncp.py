from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

import pandas as pd


# Permite importar a pasta "app" do projeto.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Configurações necessárias para importar os módulos do projeto.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/monitorame_test",
)
os.environ.setdefault(
    "LOG_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test",
)
os.environ.setdefault(
    "TCE_CE_BASE_URL",
    "https://api-dados-abertos.tce.ce.gov.br/sim",
)
os.environ.setdefault(
    "IBGE_LOCALIDADES_BASE_URL",
    "https://servicodados.ibge.gov.br/api/v1/localidades",
)
os.environ.setdefault(
    "PNCP_CONSULTA_BASE_URL",
    "https://pncp.gov.br/api/consulta",
)
os.environ.setdefault(
    "PNCP_GESTAO_BASE_URL",
    "https://pncp.gov.br/api/pncp",
)
os.environ.setdefault(
    "OPENCNPJ_BASE_URL",
    "https://kitana.opencnpj.com",
)
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_IBGE_PADRAO", "2304400")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")
os.environ.setdefault("MODALIDADE_ID_PADRAO", "6")

from app.pipeline import cleaning


class NormalizarPortePncpTest(unittest.TestCase):
    """Testa a conversão dos códigos de porte utilizados pelo PNCP."""

    def test_converte_todos_os_codigos_oficiais(self) -> None:
        casos = {
            "1": "ME",
            "2": "EPP",
            "3": "DEMAIS",
            "4": "NAO_SE_APLICA",
            "5": "NAO_INFORMADO",
        }

        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                resultado = cleaning.normalizar_porte_pncp(entrada)
                self.assertEqual(resultado, esperado)

    def test_aceita_codigos_em_formatos_numericos(self) -> None:
        casos = [
            (1, "ME"),
            (2.0, "EPP"),
            (3, "DEMAIS"),
            (4.0, "NAO_SE_APLICA"),
            (5, "NAO_INFORMADO"),
        ]

        for entrada, esperado in casos:
            with self.subTest(entrada=entrada):
                resultado = cleaning.normalizar_porte_pncp(entrada)
                self.assertEqual(resultado, esperado)

    def test_aceita_descricoes_ja_padronizadas(self) -> None:
        casos = [
            ("mei", "MEI"),
            ("Microempreendedor Individual", "MEI"),
            ("me", "ME"),
            (" epp ", "EPP"),
            ("demais", "DEMAIS"),
            ("não se aplica", "NAO_SE_APLICA"),
            ("nao-informado", "NAO_INFORMADO"),
        ]

        for entrada, esperado in casos:
            with self.subTest(entrada=entrada):
                resultado = cleaning.normalizar_porte_pncp(entrada)
                self.assertEqual(resultado, esperado)

    def test_retorna_none_para_valores_ausentes(self) -> None:
        valores_ausentes = [
            None,
            pd.NA,
            float("nan"),
            "",
            "   ",
        ]

        for entrada in valores_ausentes:
            with self.subTest(entrada=entrada):
                resultado = cleaning.normalizar_porte_pncp(entrada)
                self.assertIsNone(resultado)

    def test_retorna_none_para_valores_invalidos(self) -> None:
        valores_invalidos = [
            "6",
            "GRANDE EMPRESA",
            1.5,
            float("inf"),
        ]

        for entrada in valores_invalidos:
            with self.subTest(entrada=entrada):
                resultado = cleaning.normalizar_porte_pncp(entrada)
                self.assertIsNone(resultado)


class PadronizarPortePncpTest(unittest.TestCase):
    """Testa a criação da coluna padronizada no DataFrame."""

    def test_cria_coluna_com_portes_padronizados(self) -> None:
        entrada = pd.DataFrame(
            {
                "porte_fornecedor_id": [
                    1,
                    2,
                    3,
                    4,
                    5,
                    None,
                    "desconhecido",
                ]
            }
        )

        resultado = cleaning.padronizar_porte_pncp(entrada)

        esperado = pd.Series(
            [
                "ME",
                "EPP",
                "DEMAIS",
                "NAO_SE_APLICA",
                "NAO_INFORMADO",
                pd.NA,
                pd.NA,
            ],
            name="porte_fornecedor_padronizado",
            dtype="string",
        )

        pd.testing.assert_series_equal(
            resultado["porte_fornecedor_padronizado"],
            esperado,
        )

    def test_preserva_coluna_original(self) -> None:
        entrada = pd.DataFrame(
            {
                "porte_fornecedor_id": [1, 2]
            }
        )

        resultado = cleaning.padronizar_porte_pncp(entrada)

        self.assertEqual(
            resultado["porte_fornecedor_id"].tolist(),
            [1, 2],
        )

    def test_nao_altera_dataframe_original(self) -> None:
        entrada = pd.DataFrame(
            {
                "porte_fornecedor_id": [1, 2]
            }
        )

        resultado = cleaning.padronizar_porte_pncp(entrada)

        self.assertIsNot(resultado, entrada)

        self.assertNotIn(
            "porte_fornecedor_padronizado",
            entrada.columns,
        )

        self.assertIn(
            "porte_fornecedor_padronizado",
            resultado.columns,
        )

    def test_coluna_de_origem_nao_existe(self) -> None:
        entrada = pd.DataFrame(
            {
                "outra_coluna": [1, 2]
            }
        )

        resultado = cleaning.padronizar_porte_pncp(entrada)

        self.assertIsNot(resultado, entrada)

        pd.testing.assert_frame_equal(
            resultado,
            entrada,
        )

    def test_prioriza_nome_mei_quando_id_do_pncp_informa_me(self) -> None:
        entrada = pd.DataFrame(
            {
                "porte_fornecedor_nome": [
                    "Microempreendedor Individual"
                ],
                "porte_fornecedor_id": [1],
            }
        )

        resultado = cleaning.padronizar_porte_pncp(entrada)

        self.assertEqual(
            resultado.iloc[0]["porte_fornecedor_padronizado"],
            "MEI",
        )


class LimparResultadosPncpTest(unittest.TestCase):
    """Testa a limpeza completa dos resultados de itens do PNCP."""

    def test_limpa_resultado_e_identifica_mei_pelo_nome(self) -> None:
        registros = [
            {
                "numeroControlePNCP": "11444777000161-1-000001/2025",
                "numeroItem": 7,
                "porteFornecedorId": 1,
                "porteFornecedorNome": "Microempreendedor Individual",
                "valorTotalHomologado": "150,00",
            }
        ]

        resultado = cleaning.limpar_pncp_resultados(registros)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(
            resultado.iloc[0]["porte_fornecedor_padronizado"],
            "MEI",
        )
        self.assertEqual(
            resultado.iloc[0]["valor_total_homologado"],
            150.0,
        )


if __name__ == "__main__":
    unittest.main()

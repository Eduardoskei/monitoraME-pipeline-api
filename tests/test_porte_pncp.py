from __future__ import annotations

import unittest

import pandas as pd

from app.pipeline.cleaners import pncp as pncp_cleaning


class NormalizarPortePncpTest(unittest.TestCase):
    """Testa a conversao dos codigos de porte utilizados pelo PNCP."""

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
                self.assertEqual(pncp_cleaning.normalizar_porte_pncp(entrada), esperado)

    def test_aceita_codigos_em_formatos_numericos(self) -> None:
        casos = [(1, "ME"), (2.0, "EPP"), (3, "DEMAIS"), (4.0, "NAO_SE_APLICA"), (5, "NAO_INFORMADO")]

        for entrada, esperado in casos:
            with self.subTest(entrada=entrada):
                self.assertEqual(pncp_cleaning.normalizar_porte_pncp(entrada), esperado)

    def test_aceita_descricoes_ja_padronizadas(self) -> None:
        casos = [
            ("me", "ME"),
            (" epp ", "EPP"),
            ("demais", "DEMAIS"),
            ("nao se aplica", "NAO_SE_APLICA"),
            ("nao-informado", "NAO_INFORMADO"),
        ]

        for entrada, esperado in casos:
            with self.subTest(entrada=entrada):
                self.assertEqual(pncp_cleaning.normalizar_porte_pncp(entrada), esperado)

    def test_retorna_none_para_valores_ausentes_ou_invalidos(self) -> None:
        valores = [None, pd.NA, float("nan"), "", "   ", "6", "GRANDE EMPRESA", 1.5, float("inf")]

        for entrada in valores:
            with self.subTest(entrada=entrada):
                self.assertIsNone(pncp_cleaning.normalizar_porte_pncp(entrada))


class PadronizarPortePncpTest(unittest.TestCase):
    """Testa a criacao da coluna padronizada no DataFrame."""

    def test_cria_coluna_sem_alterar_dataframe_original(self) -> None:
        entrada = pd.DataFrame({"porte_fornecedor_id": [1, 2, 3, 4, 5, None, "desconhecido"]})

        resultado = pncp_cleaning.padronizar_porte_pncp(entrada)

        esperado = pd.Series(
            ["ME", "EPP", "DEMAIS", "NAO_SE_APLICA", "NAO_INFORMADO", pd.NA, pd.NA],
            name="porte_fornecedor_padronizado",
            dtype="string",
        )
        pd.testing.assert_series_equal(resultado["porte_fornecedor_padronizado"], esperado)
        self.assertNotIn("porte_fornecedor_padronizado", entrada.columns)

    def test_coluna_de_origem_nao_existe(self) -> None:
        entrada = pd.DataFrame({"outra_coluna": [1, 2]})

        resultado = pncp_cleaning.padronizar_porte_pncp(entrada)

        self.assertIsNot(resultado, entrada)
        pd.testing.assert_frame_equal(resultado, entrada)


class LimparResultadosPncpTest(unittest.TestCase):
    def test_extrai_resultados_dos_itens_e_padroniza_porte(self) -> None:
        registros = [
            {
                "numeroControlePNCP": "11444777000161-1-000001/2025",
                "itens": [
                    {
                        "numeroItem": 7,
                        "descricao": "Bolo",
                        "resultados": [
                            {
                                "niFornecedor": "11444777000161",
                                "porteFornecedorId": 1,
                                "valorTotalHomologado": "150,00",
                            }
                        ],
                    }
                ],
            }
        ]

        tabelas = pncp_cleaning.limpar_contratacoes(registros)

        self.assertIn("resultados", tabelas)
        self.assertNotIn("resultados", tabelas["itens"].columns)
        resultado = tabelas["resultados"].iloc[0]
        self.assertEqual(resultado["numero_controle_pncp"], "11444777000161-1-000001/2025")
        self.assertEqual(resultado["numero_item"], 7)
        self.assertEqual(resultado["porte_fornecedor_padronizado"], "ME")
        self.assertEqual(resultado["valor_total_homologado"], 150.0)


if __name__ == "__main__":
    unittest.main()

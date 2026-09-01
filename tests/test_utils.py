from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from app import utils


class UtilsTest(unittest.TestCase):
    def test_somente_digitos_preserva_apenas_numeros(self) -> None:
        self.assertEqual(utils.somente_digitos("11.444.777/0001-61"), "11444777000161")
        self.assertEqual(utils.somente_digitos(None), "")

    def test_primeiro_valor_percorre_caminhos_aninhados(self) -> None:
        registro = {"orgao": {"entidade": {"cnpj": "123"}}, "cnpj": ""}

        self.assertEqual(
            utils.primeiro_valor(
                registro,
                (
                    ("cnpj",),
                    ("orgao", "entidade", "cnpj"),
                ),
            ),
            "123",
        )

    def test_filtrar_params_vazios_remove_none_e_string_vazia(self) -> None:
        self.assertEqual(
            utils.filtrar_params_vazios({"a": None, "b": "", "c": 0, "d": False}),
            {"c": 0, "d": False},
        )

    def test_normalizar_data_usa_formatos_informados(self) -> None:
        self.assertEqual(
            utils.normalizar_data("2025-01-07", ("%Y-%m-%d", "%Y%m%d"), "%Y%m%d", "YYYY-MM-DD ou YYYYMMDD"),
            "20250107",
        )

    def test_primeira_coluna_preenchida_ignora_ausentes_nulos_e_marcadores(self) -> None:
        df = pd.DataFrame(
            {
                "a": [pd.NA, "nao_informado", "valor_a"],
                "b": ["valor_b1", "valor_b2", "valor_b3"],
            }
        )

        resultado = utils.primeira_coluna_preenchida(
            df,
            ["coluna_ausente", "a", "b"],
            marcadores_vazios=("", "nao_informado"),
        )

        self.assertEqual(resultado.tolist(), ["valor_b1", "valor_b2", "valor_a"])


if __name__ == "__main__":
    unittest.main()

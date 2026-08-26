from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


if __name__ == "__main__":
    unittest.main()

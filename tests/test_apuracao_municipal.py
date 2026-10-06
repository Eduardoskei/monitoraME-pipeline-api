from __future__ import annotations

import time
import unittest

import numpy as np
import pandas as pd

from app.pipeline import apuracao_municipal


class ApuracaoMunicipalTest(unittest.TestCase):
    def test_preserva_municipio_sem_fatos_com_valor_zero(self) -> None:
        base = pd.DataFrame(
            [
                {
                    "codigo_municipio_tce": "001",
                    "municipio_comprador": "Municipio 001",
                    "compras_me": 2,
                    "valor_me": 100.0,
                }
            ]
        )
        municipios = pd.DataFrame(
            [
                {"codigo_municipio_tce": "001", "municipio_comprador": "Municipio 001"},
                {"codigo_municipio_tce": "002", "municipio_comprador": "Municipio 002"},
            ]
        )

        resultado = apuracao_municipal.apurar_indicadores_por_municipio(
            base,
            colunas_indicadores=["compras_me", "valor_me"],
            municipios=municipios,
        )

        self.assertEqual(len(resultado), 4)
        self.assertEqual(resultado.loc[("001", "compras_me"), "valor"], 2)
        self.assertEqual(resultado.loc[("002", "compras_me"), "valor"], 0)
        self.assertTrue(resultado.index.is_unique)

    def test_rejeita_catalogo_duplicado(self) -> None:
        base = pd.DataFrame(
            [{"codigo_municipio_tce": "001", "municipio_comprador": "A", "total": 1}]
        )

        with self.assertRaisesRegex(
            apuracao_municipal.ConfiguracaoApuracaoMunicipalError,
            "duplicados",
        ):
            apuracao_municipal.apurar_indicadores_por_municipio(
                base,
                colunas_indicadores=["total", "total"],
            )


class CargaTodosMunicipiosCearensesTest(unittest.TestCase):
    MUNICIPIOS = apuracao_municipal.TOTAL_MUNICIPIOS_CEARA
    INDICADORES = apuracao_municipal.TOTAL_INDICADORES_CARGA
    FATOS_POR_MUNICIPIO = 250
    TEMPO_LIMITE_SEGUNDOS = 10.0

    def test_apura_47_indicadores_para_184_municipios_em_tempo_aceitavel(self) -> None:
        codigos = [f"{numero:03d}" for numero in range(1, self.MUNICIPIOS + 1)]
        municipios = pd.DataFrame(
            {
                "codigo_municipio_tce": codigos,
                "municipio_comprador": [f"Municipio {codigo}" for codigo in codigos],
            }
        )
        total_fatos = self.MUNICIPIOS * self.FATOS_POR_MUNICIPIO
        sequencia = np.arange(total_fatos, dtype=np.int64)
        base = pd.DataFrame(
            {
                "codigo_municipio_tce": np.repeat(codigos, self.FATOS_POR_MUNICIPIO),
                "municipio_comprador": np.repeat(
                    municipios["municipio_comprador"].to_numpy(),
                    self.FATOS_POR_MUNICIPIO,
                ),
            }
        )
        indicadores = [f"indicador_{numero:02d}" for numero in range(1, self.INDICADORES + 1)]
        for deslocamento, indicador in enumerate(indicadores):
            base[indicador] = ((sequencia + deslocamento) % 2).astype(np.int8)

        inicio = time.perf_counter()
        resultado = apuracao_municipal.apurar_indicadores_por_municipio(
            base,
            colunas_indicadores=indicadores,
            municipios=municipios,
        )
        duracao = time.perf_counter() - inicio

        self.assertEqual(len(resultado), apuracao_municipal.TOTAL_RESULTADOS_CARGA)
        self.assertEqual(resultado.attrs["municipios"], self.MUNICIPIOS)
        self.assertEqual(resultado.attrs["indicadores"], self.INDICADORES)
        self.assertTrue(resultado.index.is_unique)
        self.assertLess(
            duracao,
            self.TEMPO_LIMITE_SEGUNDOS,
            f"apuracao levou {duracao:.3f}s para {total_fatos} fatos",
        )


if __name__ == "__main__":
    unittest.main()

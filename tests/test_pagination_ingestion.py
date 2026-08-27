from __future__ import annotations

import unittest
from unittest.mock import patch

from app.pipeline.ingestion import pagination


class PaginationTest(unittest.TestCase):
    def test_start_index_respeita_limite_de_mil_registros_por_requisicao(self) -> None:
        with self.assertRaisesRegex(ValueError, "1000"):
            pagination.listar_por_start_index(lambda _start, _limit: [], tamanho_pagina=1001)

    @patch("app.pipeline.ingestion.pagination.time.sleep")
    def test_start_index_incrementa_ate_esgotar_resultados(self, sleep) -> None:
        chamadas: list[tuple[int, int]] = []
        paginas = {
            0: [{"id": indice} for indice in range(1000)],
            1000: [{"id": indice} for indice in range(1000, 2000)],
            2000: [{"id": indice} for indice in range(2000, 2300)],
        }

        def buscar_pagina(start_index: int, tamanho_pagina: int):
            chamadas.append((start_index, tamanho_pagina))
            return paginas.get(start_index, [])

        registros = pagination.listar_por_start_index(buscar_pagina)

        self.assertEqual(len(registros), 2300)
        self.assertEqual(chamadas, [(0, 1000), (1000, 1000), (2000, 1000)])
        self.assertEqual(sleep.call_count, 2)

    @patch("app.pipeline.ingestion.pagination.time.sleep")
    def test_numero_pagina_respeita_total_paginas(self, sleep) -> None:
        chamadas: list[tuple[int, int]] = []

        def buscar_pagina(pagina: int, tamanho_pagina: int):
            chamadas.append((pagina, tamanho_pagina))
            return {
                "data": [{"pagina": pagina}],
                "totalPaginas": 2,
            }

        registros = pagination.listar_por_numero_pagina(
            buscar_pagina,
            lambda dados: dados["data"],
            total_paginas=lambda dados: dados["totalPaginas"],
            tamanho_pagina=1,
        )

        self.assertEqual(registros, [{"pagina": 1}, {"pagina": 2}])
        self.assertEqual(chamadas, [(1, 1), (2, 1)])
        sleep.assert_called_once_with(pagination.THROTTLE_PAGINACAO_SEGUNDOS)


if __name__ == "__main__":
    unittest.main()

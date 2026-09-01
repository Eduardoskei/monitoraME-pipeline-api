from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
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

from app.core.config import NATUREZAS_DESPESA_CONSIDERADAS
from app.pipeline.ingestion import tce


class NaturezasDespesaTest(unittest.TestCase):
    def test_todas_as_sete_naturezas_configuradas_sao_aceitas(self) -> None:
        registros = [
            {"id": indice, "natureza_despesa": natureza}
            for indice, natureza in enumerate(NATUREZAS_DESPESA_CONSIDERADAS)
        ]

        self.assertEqual(tce.filtrar_naturezas_despesa(registros), registros)

    @patch("app.pipeline.ingestion.tce.buscar_dados_tce")
    def test_listagem_descarta_natureza_fora_do_escopo_antes_de_retornar(
        self,
        buscar_dados_tce,
    ) -> None:
        buscar_dados_tce.return_value = {
            "elements": [
                {"id": 1, "natureza_despesa": "Material de consumo"},
                {"id": 2, "natureza_despesa": "Passagens e despesas com locomocao"},
            ]
        }

        registros = tce.listar_registros(tce.ENDPOINT_ITENS, {})

        self.assertEqual(registros, [{"id": 1, "natureza_despesa": "Material de consumo"}])


if __name__ == "__main__":
    unittest.main()

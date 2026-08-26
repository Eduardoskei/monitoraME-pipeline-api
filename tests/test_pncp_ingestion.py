from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
import requests
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("PNCP_CONSULTA_BASE_URL", "https://pncp.gov.br/api/consulta")
os.environ.setdefault("PNCP_GESTAO_BASE_URL", "https://pncp.gov.br/api/pncp")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://kitana.opencnpj.com")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_IBGE_PADRAO", "2304400")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")
os.environ.setdefault("MODALIDADE_ID_PADRAO", "6")

from app.pipeline.ingestion import pncp


class PncpIngestionTest(unittest.TestCase):
    @patch("app.pipeline.ingestion.pncp.time.sleep")
    @patch("app.pipeline.ingestion.pncp.requests.get")
    def test_timeout_nao_e_mascarado_como_lista_vazia(self, get, _sleep) -> None:
        get.side_effect = requests.ReadTimeout("PNCP demorou")

        with self.assertRaises(pncp.PncpIndisponivelError):
            pncp.buscar_contratacoes_publicadas("2025-01-01", "2025-01-02", max_paginas=1)

    @patch("app.pipeline.ingestion.pncp.requests.get")
    def test_204_continua_representando_resultado_vazio_valido(self, get) -> None:
        resposta = get.return_value
        resposta.status_code = 204

        self.assertEqual(
            pncp.buscar_contratacoes_publicadas("2025-01-01", "2025-01-02", max_paginas=1),
            [],
        )

    def test_normalizar_data_pncp_aceita_iso_e_compacto(self) -> None:
        self.assertEqual(pncp.normalizar_data_pncp("2025-01-07"), "20250107")
        self.assertEqual(pncp.normalizar_data_pncp("20250107"), "20250107")

    def test_extrair_identificador_compra_usa_orgao_entidade(self) -> None:
        identificador = pncp.extrair_identificador_compra(
            {
                "orgaoEntidade": {"cnpj": "12.345.678/0001-99"},
                "anoCompra": 2025,
                "sequencialCompra": 7,
            }
        )

        self.assertEqual(identificador, pncp.IdentificadorCompra("12345678000199", 2025, 7))

    @patch("app.pipeline.ingestion.pncp.consultar_contratos_compra")
    @patch("app.pipeline.ingestion.pncp.consultar_resultados_item")
    @patch("app.pipeline.ingestion.pncp.consultar_itens_compra")
    @patch("app.pipeline.ingestion.pncp.consultar_compra")
    def test_coletar_compra_completa_retorna_payloads_brutos(
        self,
        consultar_compra,
        consultar_itens_compra,
        consultar_resultados_item,
        consultar_contratos_compra,
    ) -> None:
        consultar_compra.return_value = {"objetoCompra": "Material"}
        consultar_itens_compra.return_value = [{"numeroItem": 1, "descricao": "Caneta"}]
        consultar_resultados_item.return_value = [{"niFornecedor": "12345678000199"}]
        consultar_contratos_compra.return_value = []

        dados = pncp.coletar_compra_completa("12.345.678/0001-99", 2025, 7)

        self.assertEqual(dados["compra"], {"objetoCompra": "Material"})
        self.assertEqual(dados["itens"][0]["item"], {"numeroItem": 1, "descricao": "Caneta"})
        self.assertEqual(dados["itens"][0]["resultados"], [{"niFornecedor": "12345678000199"}])


if __name__ == "__main__":
    unittest.main()

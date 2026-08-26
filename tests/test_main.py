from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import unittest
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

from fastapi import HTTPException

from app import main
from app.pipeline.ingestion import pncp


def _route_paths(routes) -> set[str]:
    paths = set()
    for route in routes:
        path = getattr(route, "path", None)
        if path is not None:
            paths.add(path)

        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        if callable(effective_route_contexts):
            paths.update(context.path for context in effective_route_contexts())

    return paths


class MainTest(unittest.TestCase):
    def test_lifespan_inicializa_db_e_fecha_pool(self) -> None:
        async def executar_lifespan() -> None:
            with (
                patch("app.main.database.init_db") as init_db,
                patch("app.main.database.close_pool") as close_pool,
            ):
                async with main.lifespan(main.app):
                    init_db.assert_called_once_with()
                    close_pool.assert_not_called()

                close_pool.assert_called_once_with()

        asyncio.run(executar_lifespan())

    def test_lifespan_continua_sem_database_url(self) -> None:
        async def executar_lifespan() -> None:
            with (
                patch("app.main.database.init_db", side_effect=RuntimeError("DATABASE_URL nao esta definida")),
                patch("app.main.database.close_pool") as close_pool,
            ):
                async with main.lifespan(main.app):
                    close_pool.assert_not_called()

                close_pool.assert_called_once_with()

        asyncio.run(executar_lifespan())

    def test_rotas_do_pipeline_estao_registradas(self) -> None:
        rotas = _route_paths(main.app.routes)

        self.assertIn("/pipeline/pncp/contratacoes", rotas)
        self.assertIn("/pipeline/tce/contratos", rotas)
        self.assertIn("/pipeline/tce/kpis/me-por-mes", rotas)

    @patch("app.main.database.close_pool")
    @patch("app.main.database.init_db")
    @patch("app.pipeline.analisys.fornecedores.coletar_fornecedores_em_lote")
    @patch("app.pipeline.analisys.tce.buscar_contratados")
    @patch("app.pipeline.analisys.tce.buscar_contratos")
    def test_endpoint_tce_contratos_retorna_fluxo_serializado(
        self,
        buscar_contratos,
        buscar_contratados,
        coletar_fornecedores,
        _init_db,
        _close_pool,
    ) -> None:
        buscar_contratos.return_value = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "data_contrato": "2025-01-15",
                "valor_total_contrato": "1.000,00",
            }
        ]
        buscar_contratados.return_value = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "numero_documento_negociante": "11.444.777/0001-61",
                "nome_negociante": "Fornecedor Teste",
            }
        ]
        coletar_fornecedores.return_value = [
            {
                "cnpj": "11444777000161",
                "razao_social": "Fornecedor Teste",
                "opencnpj": {"porte": "MICRO EMPRESA"},
                "porte": "MICRO EMPRESA",
                "opencnpj_status": "ok",
            }
        ]

        payload = main.tce_contratos(
            data_inicial="20250101",
            data_final="20250131",
            codigo_municipio="010",
            enriquecer_fornecedores=True,
        )

        self.assertEqual(payload["totais"], {"contratos": 1})
        self.assertEqual(payload["dados"][0]["nome_negociante"], "Fornecedor Teste")
        self.assertEqual(payload["dados"][0]["fornecedor_porte_padronizado"], "ME")

    @patch("app.main.database.close_pool")
    @patch("app.main.database.init_db")
    @patch("app.main.analisys.consultar_pncp_contratacoes")
    def test_endpoint_pncp_mapeia_fonte_indisponivel_para_503(
        self,
        consultar_pncp,
        _init_db,
        _close_pool,
    ) -> None:
        consultar_pncp.side_effect = pncp.PncpIndisponivelError("PNCP indisponivel")

        with self.assertRaises(HTTPException) as contexto:
            main.pncp_contratacoes(data_inicial="20250101", data_final="20250131")

        self.assertEqual(contexto.exception.status_code, 503)
        self.assertEqual(contexto.exception.detail, "PNCP indisponivel")


if __name__ == "__main__":
    unittest.main()

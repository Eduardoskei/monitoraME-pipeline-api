from __future__ import annotations

import asyncio
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
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from fastapi import HTTPException

from app import main
from app.pipeline import analisys


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
                patch("app.main.log_database.init_log_db") as init_log_db,
                patch("app.main.log_database.close_log_pool") as close_log_pool,
            ):
                async with main.lifespan(main.app):
                    init_db.assert_called_once_with()
                    init_log_db.assert_called_once_with()
                    close_pool.assert_not_called()
                    close_log_pool.assert_not_called()

                close_pool.assert_called_once_with()
                close_log_pool.assert_called_once_with()

        asyncio.run(executar_lifespan())

    def test_lifespan_propaga_falha_de_database_url(self) -> None:
        async def executar_lifespan() -> None:
            with (
                patch("app.main.database.init_db", side_effect=RuntimeError("DATABASE_URL nao esta definida")),
                patch("app.main.database.close_pool") as close_pool,
                patch("app.main.log_database.close_log_pool") as close_log_pool,
            ):
                with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                    async with main.lifespan(main.app):
                        pass

                close_pool.assert_called_once_with()
                close_log_pool.assert_called_once_with()

        asyncio.run(executar_lifespan())

    def test_rotas_do_pipeline_estao_registradas(self) -> None:
        rotas = _route_paths(main.app.routes)

        self.assertNotIn("/pipeline/pncp/contratacoes", rotas)
        self.assertNotIn("/pipeline/pncp/ingestao-incremental", rotas)
        self.assertIn("/pipeline/tce/contratos", rotas)
        self.assertNotIn("/pipeline/tce/kpis/me-por-mes", rotas)
        self.assertIn("/pipeline/tce/kpis/portes-por-mes", rotas)
        self.assertIn("/pipeline/tce/overview", rotas)
        self.assertIn("/pipeline/tce/analitico/indicadores", rotas)

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
                "opencnpj": {"porte_empresa": "MICRO EMPRESA"},
                "porte": "MICRO EMPRESA",
                "opencnpj_status": "ok",
            }
        ]

        payload = main.tce_contratos(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
            enriquecer_fornecedores=True,
        )

        self.assertEqual(payload["totais"], {"contratos": 1})
        self.assertEqual(payload["dados"][0]["nome_negociante"], "Fornecedor Teste")
        self.assertEqual(payload["dados"][0]["fornecedor_porte_padronizado"], "ME")

    @patch("app.main.database.close_pool")
    @patch("app.main.database.init_db")
    @patch("app.main.analisys.consultar_tce_indicadores_analiticos")
    def test_endpoint_tce_analitico_indicadores_retorna_fluxo_serializado(
        self,
        consultar_indicadores,
        _init_db,
        _close_pool,
    ) -> None:
        consultar_indicadores.return_value = {
            "fonte": "TCE-CE",
            "kpi": "indicadores_analiticos_principais",
            "totais": {"resumo_geral": 1},
            "indicadores": {"resumo_geral": [{"valor_total": 1000.0}]},
        }

        payload = main.tce_analitico_indicadores(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
            limite_ranking=5,
        )

        consultar_indicadores.assert_called_once_with(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
            limite_ranking=5,
            limite=100,
        )
        self.assertEqual(payload["kpi"], "indicadores_analiticos_principais")
        self.assertEqual(payload["indicadores"]["resumo_geral"][0]["valor_total"], 1000.0)

    def test_endpoints_rejeitam_data_compacta(self) -> None:
        for endpoint in (
            main.tce_contratos,
            main.tce_kpi_portes_por_mes,
            main.tce_overview,
            main.tce_analitico_indicadores,
        ):
            with self.subTest(endpoint=endpoint.__name__):
                with self.assertRaises(HTTPException) as contexto:
                    endpoint(data_inicial="20250101", data_final="2025-01-31")

                self.assertEqual(contexto.exception.status_code, 422)
                self.assertIn("YYYY-MM-DD", contexto.exception.detail)

    @patch("app.main.analisys.consultar_overview_tce")
    def test_endpoint_tce_overview_retorna_contrato_serializado(
        self,
        consultar_overview,
    ) -> None:
        consultar_overview.return_value = {
            "fonte": "TCE-CE",
            "kpi": "overview_me_mei",
            "unidade_monetaria": "CENTAVOS",
            "kpis": {
                "percentual_participacao_me": 75.0,
                "total_compras_consideradas_centavos": 100_000,
                "percentual_compras_fornecedores_locais": 50.0,
                "percentual_recursos_fora_municipio": 40.0,
            },
            "evolucao_compras_consideradas": [],
            "destino_recursos": [],
        }

        payload = main.tce_overview(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
        )

        consultar_overview.assert_called_once_with(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
        )
        self.assertEqual(payload["kpi"], "overview_me_mei")
        self.assertEqual(
            payload["kpis"]["total_compras_consideradas_centavos"],
            100_000,
        )

    @patch("app.main.analisys.consultar_tce_indicadores_analiticos")
    def test_endpoint_analitico_mapeia_falha_total_de_competencias_para_503(
        self,
        consultar_indicadores,
    ) -> None:
        consultar_indicadores.side_effect = analisys.CompetenciasTceIndisponiveisError(
            "Nenhuma competência disponível."
        )

        with self.assertRaises(HTTPException) as contexto:
            main.tce_analitico_indicadores(
                data_inicial="2025-01-01",
                data_final="2025-01-31",
            )

        self.assertEqual(contexto.exception.status_code, 503)
        self.assertEqual(contexto.exception.detail, "Nenhuma competência disponível.")



if __name__ == "__main__":
    unittest.main()

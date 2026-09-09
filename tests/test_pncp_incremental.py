from __future__ import annotations

from datetime import datetime, timezone
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

from app.pipeline import pncp_incremental
from app.pipeline.persistence.pncp import ResultadoPersistencia


class PncpIncrementalTest(unittest.TestCase):
    @patch("app.pipeline.pncp_incremental.database.buscar_checkpoint_pncp", return_value=None)
    def test_primeira_execucao_usa_janela_inicial_configurada(self, _checkpoint) -> None:
        agora = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)

        janela = pncp_incremental.calcular_janela_incremental(
            escopo="pncp:teste",
            agora=agora,
            janela_inicial_horas=6,
        )

        self.assertEqual(janela.inicio, datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(janela.fim, agora)
        self.assertIsNone(janela.checkpoint_anterior)

    @patch(
        "app.pipeline.pncp_incremental.database.buscar_checkpoint_pncp",
        return_value=datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc),
    )
    def test_execucao_incremental_usa_checkpoint_quando_existente(self, _checkpoint) -> None:
        agora = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)

        janela = pncp_incremental.calcular_janela_incremental(
            escopo="pncp:teste",
            agora=agora,
            janela_inicial_horas=6,
        )

        self.assertEqual(janela.inicio, datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc))
        self.assertEqual(janela.fim, agora)

    def test_filtro_temporal_preserva_dia_do_checkpoint_quando_fonte_so_tem_data(self) -> None:
        inicio = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)

        self.assertTrue(
            pncp_incremental._registro_alterado_desde(
                {"dataAtualizacao": "2026-09-09"},
                inicio,
                pncp_incremental.CONTRATACAO_CAMPOS_CONTROLE_TEMPORAL,
            )
        )
        self.assertFalse(
            pncp_incremental._registro_alterado_desde(
                {"dataAtualizacao": "2026-09-08"},
                inicio,
                pncp_incremental.CONTRATACAO_CAMPOS_CONTROLE_TEMPORAL,
            )
        )

    def test_preparar_tabelas_filtra_por_naturezas_configuradas(self) -> None:
        tabelas = pncp_incremental._preparar_tabelas(
            contratacoes=[
                {
                    "numeroControlePNCP": "12345678000199-1-000001/2026",
                    "anoCompra": 2026,
                    "sequencialCompra": 1,
                    "objetoCompra": "Compra geral",
                    "itens": [
                        {
                            "numeroItem": 1,
                            "descricao": "Material de consumo para escola",
                        }
                    ],
                },
                {
                    "numeroControlePNCP": "12345678000199-1-000002/2026",
                    "anoCompra": 2026,
                    "sequencialCompra": 2,
                    "objetoCompra": "Locacao de veiculos",
                    "itens": [{"numeroItem": 1, "descricao": "Veiculo utilitario"}],
                },
            ],
            pca_planos=[],
            pca_itens=[],
        )

        self.assertEqual(tabelas["contratacoes"]["numero_controle_pncp"].tolist(), ["12345678000199-1-000001/2026"])
        self.assertEqual(tabelas["itens"]["natureza_despesa_monitorada"].tolist(), ["Material de consumo"])

    @patch("app.pipeline.pncp_incremental.database.registrar_sucesso_e_checkpoint_pncp")
    @patch("app.pipeline.pncp_incremental.pncp_persistence.persistir_tabelas")
    @patch("app.pipeline.pncp_incremental.pncp.buscar_pcas_atualizados", return_value=[])
    @patch("app.pipeline.pncp_incremental.pncp.montar_registro_compra_completo", side_effect=lambda registro: registro)
    @patch("app.pipeline.pncp_incremental.pncp.buscar_contratacoes_atualizadas")
    @patch("app.pipeline.pncp_incremental.pncp.buscar_contratacoes_publicadas")
    @patch("app.pipeline.pncp_incremental.database.buscar_checkpoint_pncp", return_value=None)
    def test_execucao_sucesso_persiste_e_avanca_checkpoint(
        self,
        _checkpoint,
        buscar_publicadas,
        buscar_atualizadas,
        _montar_completo,
        _buscar_pca,
        persistir_tabelas,
        registrar_sucesso_e_checkpoint,
    ) -> None:
        agora = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)
        publicacao = {
            "numeroControlePNCP": "12345678000199-1-000001/2026",
            "anoCompra": 2026,
            "sequencialCompra": 1,
            "dataPublicacaoPncp": "2026-09-09",
            "objetoCompra": "Material de consumo",
        }
        buscar_publicadas.return_value = [publicacao]
        buscar_atualizadas.return_value = [publicacao]
        persistir_tabelas.return_value = ResultadoPersistencia(inseridos=1, atualizados=1)

        resultado = pncp_incremental.executar_ingestao_incremental_pncp(
            agora=agora,
            janela_inicial_horas=6,
            max_paginas=1,
        )

        buscar_publicadas.assert_called_once()
        args, kwargs = buscar_publicadas.call_args
        self.assertEqual(args, ("20260909", "20260909"))
        self.assertEqual(kwargs["modalidade_id"], 6)
        self.assertEqual(kwargs["uf"], "CE")
        self.assertEqual(kwargs["max_paginas"], 1)
        self.assertEqual(resultado["quantidade_lida"], 2)
        self.assertEqual(resultado["quantidade_inserida"], 1)
        self.assertEqual(resultado["quantidade_atualizada"], 1)
        registrar_sucesso_e_checkpoint.assert_called_once()
        self.assertEqual(registrar_sucesso_e_checkpoint.call_args.kwargs["janela_fim"], agora)

    @patch("app.pipeline.pncp_incremental.database.salvar_checkpoint_pncp")
    @patch("app.pipeline.pncp_incremental.database.registrar_execucao_pncp")
    @patch("app.pipeline.pncp_incremental.database.registrar_sucesso_e_checkpoint_pncp")
    @patch("app.pipeline.pncp_incremental.pncp_persistence.persistir_tabelas", side_effect=RuntimeError("db falhou"))
    @patch("app.pipeline.pncp_incremental.pncp.buscar_pcas_atualizados", return_value=[])
    @patch("app.pipeline.pncp_incremental.pncp.montar_registro_compra_completo", side_effect=lambda registro: registro)
    @patch("app.pipeline.pncp_incremental.pncp.buscar_contratacoes_atualizadas", return_value=[])
    @patch("app.pipeline.pncp_incremental.pncp.buscar_contratacoes_publicadas")
    @patch("app.pipeline.pncp_incremental.database.buscar_checkpoint_pncp", return_value=None)
    def test_execucao_falha_nao_avanca_checkpoint(
        self,
        _checkpoint,
        buscar_publicadas,
        _buscar_atualizadas,
        _montar_completo,
        _buscar_pca,
        _persistir_tabelas,
        registrar_sucesso_e_checkpoint,
        registrar_execucao,
        salvar_checkpoint,
    ) -> None:
        buscar_publicadas.return_value = [
            {
                "numeroControlePNCP": "12345678000199-1-000001/2026",
                "dataPublicacaoPncp": "2026-09-09",
                "objetoCompra": "Material de consumo",
            }
        ]

        with self.assertRaisesRegex(RuntimeError, "db falhou"):
            pncp_incremental.executar_ingestao_incremental_pncp(
                agora=datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc),
                janela_inicial_horas=6,
                max_paginas=1,
            )

        registrar_execucao.assert_called_once()
        self.assertEqual(registrar_execucao.call_args.kwargs["status_execucao"], "falha")
        self.assertEqual(registrar_execucao.call_args.kwargs["quantidade_com_erro"], 1)
        registrar_sucesso_e_checkpoint.assert_not_called()
        salvar_checkpoint.assert_not_called()


if __name__ == "__main__":
    unittest.main()

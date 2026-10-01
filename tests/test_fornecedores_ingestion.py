from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import unittest
import requests
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from app.pipeline.ingestion import fornecedores


class FornecedoresIngestionTest(unittest.TestCase):
    def test_somente_digitos_limpa_cnpj_para_chamadas(self) -> None:
        self.assertEqual(fornecedores.somente_digitos("12.345.678/0001-99"), "12345678000199")

    def test_normalizar_porte_me_identifica_microempresa(self) -> None:
        self.assertEqual(fornecedores.normalizar_porte_me("ME"), "ME")
        self.assertEqual(fornecedores.normalizar_porte_me("MICRO EMPRESA"), "ME")
        self.assertEqual(fornecedores.normalizar_porte_me("micro-empresa"), "ME")
        self.assertEqual(fornecedores.normalizar_porte_me("Microempresa (ME)"), "ME")
        self.assertIsNone(fornecedores.normalizar_porte_me("EPP"))

    def test_extracao_usa_apenas_campos_receita_do_opencnpj_org(self) -> None:
        self.assertEqual(
            fornecedores.extrair_razao_social({"razao_social": "EMPRESA TESTE LTDA"}),
            "EMPRESA TESTE LTDA",
        )
        self.assertEqual(fornecedores.extrair_porte_cadastral({"porte_empresa": "MICRO EMPRESA"}), "MICRO EMPRESA")
        self.assertIsNone(fornecedores.extrair_razao_social({"razaoSocial": "EMPRESA TESTE LTDA"}))
        self.assertIsNone(fornecedores.extrair_porte_cadastral({"porte": "MICRO EMPRESA"}))

    @patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj")
    def test_coletar_fornecedor_retorna_payload_bruto(self, buscar_opencnpj) -> None:
        buscar_opencnpj.return_value = {"porte_empresa": "ME", "razao_social": "EMPRESA TESTE LTDA"}

        dados = fornecedores.coletar_fornecedor("12.345.678/0001-99")

        self.assertEqual(dados["cnpj"], "12345678000199")
        self.assertEqual(dados["opencnpj"], {"porte_empresa": "ME", "razao_social": "EMPRESA TESTE LTDA"})
        self.assertEqual(dados["razao_social"], "EMPRESA TESTE LTDA")
        self.assertEqual(dados["porte"], "ME")
        self.assertEqual(dados["porte_fonte"], "opencnpj")
        self.assertEqual(dados["opencnpj_status"], "ok")

    @patch("app.pipeline.ingestion.fornecedores.requests.get")
    def test_buscar_opencnpj_usa_endpoint_receita_do_opencnpj_org(self, get) -> None:
        response = get.return_value
        response.status_code = 200
        response.json.return_value = {
            "cnpj": "ABCDEF12345678",
            "razao_social": "EMPRESA TESTE LTDA",
            "porte_empresa": "MICRO EMPRESA",
        }

        with patch.object(fornecedores, "OPENCNPJ_URL", "https://api.opencnpj.org"):
            dados = fornecedores.buscar_opencnpj("ab.cde.f12/3456-78")

        self.assertEqual(dados["porte_empresa"], "MICRO EMPRESA")
        get.assert_called_once_with(
            "https://api.opencnpj.org/ABCDEF12345678",
            params={"datasets": "receita"},
            timeout=(5, 25),
        )

    @patch("app.pipeline.ingestion.fornecedores.requests.get")
    def test_buscar_opencnpj_usa_sessao_recebida(self, get) -> None:
        session = Mock(spec=requests.Session)
        response = session.get.return_value
        response.status_code = 200
        response.json.return_value = {"porte_empresa": "ME"}

        with patch.object(fornecedores, "OPENCNPJ_URL", "https://api.opencnpj.org"):
            dados = fornecedores.buscar_opencnpj(
                "11.444.777/0001-61",
                session=session,
            )

        self.assertEqual(dados, {"porte_empresa": "ME"})
        session.get.assert_called_once_with(
            "https://api.opencnpj.org/11444777000161",
            params={"datasets": "receita"},
            timeout=(5, 25),
        )
        get.assert_not_called()

    @patch("app.pipeline.ingestion.fornecedores.time.sleep")
    @patch("app.pipeline.ingestion.fornecedores.requests.get")
    def test_falha_da_fonte_nao_e_mascarada_como_cnpj_sem_dados(self, get, _sleep) -> None:
        get.side_effect = requests.ReadTimeout("fonte demorou")

        with self.assertRaises(fornecedores.FonteCadastralIndisponivelError):
            fornecedores._get_json("https://fonte.test/cnpj", max_retries=1)

    @patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj")
    def test_coleta_marca_opencnpj_indisponivel(self, opencnpj) -> None:
        opencnpj.side_effect = fornecedores.FonteCadastralIndisponivelError("timeout")

        dados = fornecedores.coletar_fornecedor("11.444.777/0001-61")

        self.assertEqual(dados["opencnpj_status"], "indisponivel")
        self.assertEqual(dados["opencnpj"], {})
        self.assertIsNone(dados["porte"])
        self.assertIsNone(dados["porte_fonte"])

    @patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj")
    @patch("app.pipeline.ingestion.fornecedores.database.localizar_fornecedor_me")
    def test_validar_fornecedor_me_usa_banco_sem_chamar_apis(
        self,
        localizar_fornecedor_me,
        buscar_opencnpj,
    ) -> None:
        fornecedor = {
            "cnpj": "12345678000199",
            "razao_social": "EMPRESA TESTE LTDA",
            "porte": "ME",
        }
        localizar_fornecedor_me.return_value = fornecedor

        dados = fornecedores.validar_fornecedor_me("12.345.678/0001-99")

        self.assertEqual(dados, fornecedor)
        buscar_opencnpj.assert_not_called()

    @patch("app.pipeline.ingestion.fornecedores.database.salvar_fornecedor_me")
    @patch("app.pipeline.ingestion.fornecedores.database.localizar_fornecedor_me")
    @patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj")
    def test_validar_fornecedor_me_salva_apenas_quando_porte_e_me(
        self,
        buscar_opencnpj,
        localizar_fornecedor_me,
        salvar_fornecedor_me,
    ) -> None:
        localizar_fornecedor_me.return_value = None
        buscar_opencnpj.return_value = {
            "cnpj": "12345678000199",
            "razao_social": "EMPRESA TESTE LTDA",
            "porte_empresa": "MICRO EMPRESA",
        }

        dados = fornecedores.validar_fornecedor_me("12.345.678/0001-99")

        self.assertEqual(
            dados,
            {
                "cnpj": "12345678000199",
                "razao_social": "EMPRESA TESTE LTDA",
                "porte": "ME",
            },
        )
        salvar_fornecedor_me.assert_called_once_with("12345678000199", "EMPRESA TESTE LTDA", "ME")

    @patch("app.pipeline.ingestion.fornecedores.database.salvar_fornecedor_me")
    @patch("app.pipeline.ingestion.fornecedores.database.localizar_fornecedor_me")
    @patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj")
    def test_validar_fornecedor_me_nao_salva_empresa_que_nao_e_me(
        self,
        buscar_opencnpj,
        localizar_fornecedor_me,
        salvar_fornecedor_me,
    ) -> None:
        localizar_fornecedor_me.return_value = None
        buscar_opencnpj.return_value = {
            "razao_social": "EMPRESA MEDIA LTDA",
            "porte_empresa": "DEMAIS",
        }

        dados = fornecedores.validar_fornecedor_me("12.345.678/0001-99")

        self.assertIsNone(dados)
        salvar_fornecedor_me.assert_not_called()

    @patch("app.pipeline.ingestion.fornecedores.time.sleep")
    @patch("app.pipeline.ingestion.fornecedores.coletar_fornecedor")
    def test_coletar_fornecedores_em_lote_ignora_duplicatas_e_pausa_entre_chamadas(
        self, mock_coletar, mock_sleep
    ) -> None:
        mock_coletar.side_effect = lambda cnpj, **_kwargs: {"cnpj": cnpj}

        resultados = fornecedores.coletar_fornecedores_em_lote(
            # os 2 primeiros sao o mesmo CNPJ em formatos diferentes -> so 1 chamada
            ["11444777000161", "11.444.777/0001-61", "98765432000199"],
            throttle_segundos=0.1,
        )

        self.assertEqual(mock_coletar.call_count, 2)
        self.assertEqual({r["cnpj"] for r in resultados}, {"11444777000161", "98765432000199"})
        self.assertEqual(mock_sleep.call_count, 1)
        mock_sleep.assert_called_with(0.1)

    @patch("app.pipeline.ingestion.fornecedores.time.sleep")
    @patch("app.pipeline.ingestion.fornecedores.coletar_fornecedor")
    def test_coletar_fornecedores_em_lote_nao_dorme_depois_do_ultimo(
        self, mock_coletar, mock_sleep
    ) -> None:
        mock_coletar.side_effect = lambda cnpj, **_kwargs: {"cnpj": cnpj}

        fornecedores.coletar_fornecedores_em_lote(
            ["11444777000161"],
            throttle_segundos=0.1,
        )

        mock_sleep.assert_not_called()

    @patch("app.pipeline.ingestion.fornecedores.time.sleep")
    @patch("app.pipeline.ingestion.fornecedores.coletar_fornecedor")
    def test_coletar_fornecedores_em_lote_sem_throttle_nao_dorme(self, mock_coletar, mock_sleep) -> None:
        mock_coletar.side_effect = lambda cnpj, **_kwargs: {"cnpj": cnpj}

        fornecedores.coletar_fornecedores_em_lote(["11444777000161"], throttle_segundos=0)

        mock_sleep.assert_not_called()

    @patch("app.pipeline.ingestion.fornecedores.requests.Session")
    @patch("app.pipeline.ingestion.fornecedores.coletar_fornecedor")
    def test_coleta_em_lote_reutiliza_a_mesma_sessao_http(
        self,
        mock_coletar,
        session_factory,
    ) -> None:
        session = session_factory.return_value.__enter__.return_value
        mock_coletar.side_effect = lambda cnpj, **_kwargs: {"cnpj": cnpj}

        fornecedores.coletar_fornecedores_em_lote(
            ["11444777000161", "98765432000199"],
            throttle_segundos=0,
        )

        session_factory.assert_called_once_with()
        self.assertEqual(mock_coletar.call_count, 2)
        for chamada in mock_coletar.call_args_list:
            self.assertIs(chamada.kwargs["session"], session)


class FornecedoresCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.agora = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
        self.cnpj = "11444777000161"
        self.dados_normalizados = {
            "cnpj": self.cnpj,
            "razao_social": "EMPRESA TESTE LTDA",
            "porte_padronizado": "ME",
            "opencnpj_status": "ok",
        }

    def test_politica_a_define_ttl_por_status(self) -> None:
        self.assertEqual(fornecedores._ttl_cache_status("ok"), timedelta(days=30))
        self.assertEqual(
            fornecedores._ttl_cache_status("nao_encontrado"),
            timedelta(days=7),
        )
        self.assertEqual(
            fornecedores._ttl_cache_status("indisponivel"),
            timedelta(hours=1),
        )

    def _cache(self, *, expira_em, cache_desatualizado=False):
        return {
            "cnpj": self.cnpj,
            "opencnpj_status": "ok",
            "dados_normalizados": self.dados_normalizados,
            "payload": {"razao_social": "EMPRESA TESTE LTDA", "porte_empresa": "ME"},
            "observado_em": self.agora - timedelta(days=1),
            "expira_em": expira_em,
            "ultima_tentativa_em": self.agora - timedelta(days=1),
            "ultima_tentativa_status": "ok",
            "cache_desatualizado": cache_desatualizado,
            "atualizado_em": self.agora - timedelta(days=1),
        }

    @patch("app.pipeline.ingestion.fornecedores.coletar_fornecedores_em_lote")
    @patch("app.pipeline.ingestion.fornecedores.database.salvar_fornecedores_cache")
    @patch("app.pipeline.ingestion.fornecedores.database.listar_fornecedores_cache")
    @patch("app.pipeline.ingestion.fornecedores._agora_utc")
    def test_cache_valido_nao_consulta_opencnpj(
        self,
        agora_utc,
        listar_cache,
        salvar_cache,
        coletar_lote,
    ) -> None:
        agora_utc.return_value = self.agora
        listar_cache.return_value = {
            self.cnpj: self._cache(expira_em=self.agora + timedelta(days=1))
        }

        resultado = fornecedores.obter_fornecedores_com_cache([self.cnpj])

        self.assertEqual(resultado.iloc[0]["porte_padronizado"], "ME")
        coletar_lote.assert_not_called()
        salvar_cache.assert_not_called()

    @patch("app.pipeline.ingestion.fornecedores.database.salvar_fornecedores_cache")
    @patch("app.pipeline.ingestion.fornecedores.database.listar_fornecedores_cache", return_value={})
    @patch("app.pipeline.ingestion.fornecedores.coletar_fornecedores_em_lote")
    @patch("app.pipeline.ingestion.fornecedores._agora_utc")
    def test_cache_miss_persiste_dados_normalizados_e_payload_por_trinta_dias(
        self,
        agora_utc,
        coletar_lote,
        listar_cache,
        salvar_cache,
    ) -> None:
        agora_utc.return_value = self.agora
        coletar_lote.return_value = [
            {
                "cnpj": self.cnpj,
                "razao_social": "EMPRESA TESTE LTDA",
                "porte": "ME",
                "opencnpj_status": "ok",
                "opencnpj": {
                    "razao_social": "EMPRESA TESTE LTDA",
                    "porte_empresa": "ME",
                },
            }
        ]

        resultado = fornecedores.obter_fornecedores_com_cache(
            [self.cnpj],
            throttle_segundos=0,
        )

        self.assertEqual(resultado.iloc[0]["porte_padronizado"], "ME")
        registro = salvar_cache.call_args.args[0][0]
        self.assertEqual(registro["payload"]["porte_empresa"], "ME")
        self.assertEqual(registro["dados_normalizados"]["porte_padronizado"], "ME")
        self.assertEqual(registro["expira_em"], self.agora + timedelta(days=30))
        self.assertFalse(registro["cache_desatualizado"])

    @patch("app.pipeline.ingestion.fornecedores.database.salvar_fornecedores_cache")
    @patch("app.pipeline.ingestion.fornecedores.database.listar_fornecedores_cache")
    @patch("app.pipeline.ingestion.fornecedores.coletar_fornecedores_em_lote")
    @patch("app.pipeline.ingestion.fornecedores._agora_utc")
    def test_falha_ao_renovar_preserva_dado_expirado_por_uma_hora(
        self,
        agora_utc,
        coletar_lote,
        listar_cache,
        salvar_cache,
    ) -> None:
        agora_utc.return_value = self.agora
        listar_cache.return_value = {
            self.cnpj: self._cache(expira_em=self.agora - timedelta(seconds=1))
        }
        coletar_lote.return_value = [
            {
                "cnpj": self.cnpj,
                "opencnpj": {},
                "opencnpj_status": "indisponivel",
            }
        ]

        resultado = fornecedores.obter_fornecedores_com_cache([self.cnpj])

        self.assertEqual(resultado.iloc[0]["porte_padronizado"], "ME")
        self.assertTrue(resultado.iloc[0]["cache_desatualizado"])
        registro = salvar_cache.call_args.args[0][0]
        self.assertEqual(registro["dados_normalizados"], self.dados_normalizados)
        self.assertEqual(registro["expira_em"], self.agora + timedelta(hours=1))
        self.assertEqual(registro["ultima_tentativa_status"], "indisponivel")


if __name__ == "__main__":
    unittest.main()

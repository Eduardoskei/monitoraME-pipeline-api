from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "057")

from app import main
from app.pipeline import empenhos, territorial


def _registro(numero: str, bruto: int, anulado: int, documento: str) -> dict[str, object]:
    return {
        "codigo_municipio_tce": "057",
        "chave_empenho": f"057|202500|01|01|2025-01-10|{numero}",
        "exercicio_orcamento": 202500,
        "codigo_orgao": "01",
        "codigo_unidade_orcamentaria": "01",
        "data_empenho": "2025-01-10",
        "numero_empenho": numero,
        "codigo_natureza_despesa": "33903900",
        "codigo_elemento_despesa": "39",
        "valor_empenhado_centavos": bruto,
        "valor_anulado_centavos": anulado,
        "valor_liquido_centavos": bruto - anulado,
        "tipo_documento_fornecedor": "1",
        "documento_fornecedor": documento,
        "cnpj_fornecedor": documento,
        "cpf_fornecedor": None,
        "nome_fornecedor": f"Fornecedor {numero}",
        "municipio_fornecedor_informado": None,
        "uf_fornecedor_informada": None,
        "estado_empenho": "OR",
    }


def _cache(cnpj: str, porte: str, municipio: str, uf: str) -> dict[str, object]:
    return {
        "cnpj": cnpj,
        "dados_normalizados": {
            "cnpj": cnpj,
            "porte_padronizado": porte,
            "municipio_sede": municipio,
            "uf_sede": uf,
            "optante_mei": porte == "MEI",
            "mei_discriminado": True,
        },
    }


class AnaliseTerritorialTest(unittest.TestCase):
    @patch("app.api.endpoints.pipeline.territorial.consultar_analise_territorial")
    def test_endpoint_repassa_filtros_e_request_id(self, consultar) -> None:
        consultar.return_value = {"fonte": "TCE-CE", "consultas_externas": False}
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/pipeline/tce/analise-territorial",
            "headers": [(b"x-request-id", b"req_rota_territorial")],
        })
        response = Response()

        resultado = main.tce_analise_territorial(
            request=request,
            response=response,
            start_date="2025-01-01",
            end_date="2025-12-31",
            uf="CE",
            municipality_tce_code="057",
            company_sizes=[],
            supplier_origins=[],
            expense_element_codes=[],
            ranking_limit=5,
        )

        self.assertFalse(resultado["consultas_externas"])
        self.assertEqual(response.headers["X-Request-ID"], "req_rota_territorial")
        consultar.assert_called_once_with(
            start_date="2025-01-01",
            end_date="2025-12-31",
            uf="CE",
            municipality_tce_code="057",
            company_sizes=[],
            supplier_origins=[],
            expense_element_codes=[],
            ranking_limit=5,
        )

    @patch("app.pipeline.territorial.database.listar_fornecedores_cache")
    @patch("app.pipeline.territorial.persistence.listar_empenhos_liquidos_publicados")
    @patch("app.pipeline.territorial.persistence.listar_publicacoes_competencias")
    @patch("app.pipeline.empenhos.database.localizar_municipio_tce")
    def test_calcula_fluxos_concentracao_e_cobertura(
        self, localizar, listar_publicacoes, listar_empenhos, listar_cache
    ) -> None:
        localizar.return_value = {
            "codigo_municipio_tce": "057",
            "codigo_municipio_ibge": "2304400",
            "nome": "Fortaleza",
            "uf": "CE",
        }
        listar_publicacoes.return_value = {"2025-01": None}
        listar_empenhos.return_value = [
            _registro("1", 100_000, 10_000, "11111111000111"),
            _registro("2", 50_000, 0, "22222222000122"),
            _registro("3", 40_000, 0, "33333333000133"),
            _registro("4", 20_000, 0, "44444444000144"),
        ]
        listar_cache.return_value = {
            "11111111000111": _cache("11111111000111", "ME", "Fortaleza", "CE"),
            "22222222000122": _cache("22222222000122", "EPP", "Caucaia", "CE"),
            "33333333000133": _cache("33333333000133", "DEMAIS", "Sao Paulo", "SP"),
        }

        resultado = territorial.consultar_analise_territorial(
            start_date="2025-01-01",
            end_date="2025-01-31",
            uf="CE",
            municipality_tce_code="057",
            company_sizes=[],
            supplier_origins=[],
            expense_element_codes=[],
            ranking_limit=3,
        )

        self.assertFalse(resultado["consultas_externas"])
        self.assertEqual(resultado["resumo_financeiro"]["valor_liquido_centavos"], 200_000)
        indicadores = resultado["indicadores_territoriais"]
        self.assertEqual(indicadores["valor_local_centavos"], 90_000)
        self.assertEqual(indicadores["valor_outros_municipios_ce_centavos"], 50_000)
        self.assertEqual(indicadores["valor_fora_ceara_centavos"], 40_000)
        self.assertEqual(indicadores["valor_origem_nao_identificada_centavos"], 20_000)
        self.assertEqual(indicadores["retencao_local_percentual"], 45.0)
        self.assertEqual(indicadores["retencao_local_origem_conhecida_percentual"], 50.0)
        self.assertEqual(indicadores["retencao_ceara_percentual"], 70.0)
        self.assertEqual(indicadores["participacao_me_mei_percentual"], 50.0)
        self.assertEqual(resultado["cobertura"]["cobertura_cadastral_percentual"], 75.0)
        self.assertEqual(resultado["cobertura"]["cobertura_geografica_por_valor_percentual"], 90.0)
        self.assertEqual(len(resultado["ranking_fornecedores"]), 3)
        self.assertEqual(resultado["ranking_fornecedores"][0]["valor_liquido_centavos"], 90_000)
        self.assertEqual(resultado["evolucao_mensal"][0]["retencao_local_percentual"], 45.0)

    @patch("app.pipeline.territorial.persistence.listar_publicacoes_competencias", return_value={})
    @patch("app.pipeline.empenhos.database.localizar_municipio_tce")
    def test_rejeita_periodo_sem_competencia_publicada(self, localizar, _publicacoes) -> None:
        localizar.return_value = {
            "codigo_municipio_tce": "057",
            "codigo_municipio_ibge": "2304400",
            "nome": "Fortaleza",
            "uf": "CE",
        }
        with self.assertRaises(empenhos.EmpenhoRequestError) as contexto:
            territorial.consultar_analise_territorial(
                start_date="2025-01-01",
                end_date="2025-01-31",
                uf="CE",
                municipality_tce_code="057",
                company_sizes=[],
                supplier_origins=[],
                expense_element_codes=[],
                ranking_limit=10,
            )
        self.assertEqual(contexto.exception.code, "TERRITORIAL_DATA_NOT_AVAILABLE")

    def test_validacao_da_rota_usa_envelope_padronizado(self) -> None:
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/pipeline/tce/analise-territorial",
            "headers": [(b"x-request-id", b"req_territorial")],
        })
        error = RequestValidationError([{
            "type": "greater_than_equal",
            "loc": ("query", "ranking_limit"),
            "msg": "Input should be greater than or equal to 1",
            "input": 0,
        }])
        resposta = asyncio.run(main.validation_exception_handler(request, error))
        corpo = json.loads(resposta.body)
        self.assertEqual(corpo["error"]["code"], "INVALID_RANKING_LIMIT")
        self.assertEqual(corpo["error"]["request_id"], "req_territorial")


if __name__ == "__main__":
    unittest.main()

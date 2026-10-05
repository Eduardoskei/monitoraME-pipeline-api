from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from app import main
from app.api.schemas.comparisons import TceComparisonRequest
from app.pipeline import comparisons, kpis


def _filtros(codigo_ibge: str, inicio: str, fim: str) -> dict[str, object]:
    return {
        "uf": "CE",
        "municipality_ibge_code": codigo_ibge,
        "start_date": inicio,
        "end_date": fim,
        "company_sizes": [],
        "supplier_origins": [],
        "expense_element_codes": [],
    }


def _payload() -> dict[str, object]:
    return {
        "comparison_mode": "MIXED",
        "left": {
            "label": "Acarau 2025",
            "filters": _filtros("2300200", "2025-01-01", "2025-01-31"),
        },
        "right": {
            "label": "Sobral 2024",
            "filters": _filtros("2312908", "2024-01-01", "2024-01-31"),
        },
    }


def _empenho(codigo_tce: str, data_empenho: str, valor: int, cnpj: str) -> dict[str, object]:
    return {
        "codigo_municipio_tce": codigo_tce,
        "chave_empenho": f"{codigo_tce}|202500|17|01|{data_empenho}|1",
        "exercicio_orcamento": 202500,
        "codigo_orgao": "17",
        "codigo_unidade_orcamentaria": "01",
        "data_empenho": data_empenho,
        "numero_empenho": "1",
        "codigo_natureza_despesa": "33903000",
        "codigo_elemento_despesa": "30",
        "valor_empenhado_centavos": valor,
        "valor_anulado_centavos": 0,
        "valor_liquido_centavos": valor,
        "tipo_documento_fornecedor": "1",
        "documento_fornecedor": cnpj,
        "cnpj_fornecedor": cnpj,
        "cpf_fornecedor": None,
        "nome_fornecedor": f"Fornecedor {codigo_tce}",
        "municipio_fornecedor_informado": None,
        "uf_fornecedor_informada": None,
        "estado_empenho": "OR",
    }


def _cache(cnpj: str, porte: str, municipio: str) -> dict[str, object]:
    agora = datetime(2026, 10, 5, tzinfo=timezone.utc)
    return {
        "cnpj": cnpj,
        "dados_normalizados": {
            "cnpj": cnpj,
            "porte_padronizado": porte,
            "optante_mei": porte == "MEI",
            "mei_discriminado": True,
            "fonte_porte": "opencnpj",
            "procedencia_porte": "cadastro",
            "observado_em": agora.isoformat(),
            "municipio_sede": municipio,
            "uf_sede": "CE",
            "cnae_principal_codigo": "6201501",
            "cnae_principal_descricao": "Desenvolvimento de programas",
            "opencnpj_status": "ok",
        },
        "cache_desatualizado": False,
        "ultima_tentativa_status": "ok",
    }


class ComparacaoTceTest(unittest.TestCase):
    def test_aplica_filtros_independentes_do_lado(self) -> None:
        base = pd.DataFrame(
            [
                {
                    "porte_fornecedor": "ME",
                    "origem_geografica": kpis.ORIGEM_FORNECEDOR_LOCAL,
                    "natureza_despesa_codigo": "30",
                },
                {
                    "porte_fornecedor": "DEMAIS",
                    "origem_geografica": kpis.ORIGEM_FORNECEDOR_FORA_CE,
                    "natureza_despesa_codigo": "39",
                },
            ]
        )

        resultado = comparisons._aplicar_filtros(
            base,
            {
                "company_sizes": ["OTHER"],
                "supplier_origins": ["FORA_DO_CEARA"],
                "expense_element_codes": ["39"],
            },
        )

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado.iloc[0]["porte_fornecedor"], "DEMAIS")

    @patch("app.pipeline.analisys._atualizar_competencias_tce")
    @patch("app.pipeline.ingestion.fornecedores.obter_fornecedores_com_cache")
    @patch("app.pipeline.comparisons.database.listar_fornecedores_cache")
    @patch("app.pipeline.comparisons.tce_despesas_persistence.listar_empenhos_liquidos_publicados")
    @patch("app.pipeline.comparisons.tce_despesas_persistence.listar_publicacoes_competencias")
    @patch("app.pipeline.comparisons.database.localizar_municipio_tce_por_ibge")
    def test_compara_overviews_usando_somente_o_banco(
        self,
        localizar_municipio,
        listar_publicacoes,
        listar_empenhos,
        listar_cache,
        obter_fornecedores,
        atualizar_competencias,
    ) -> None:
        municipios = {
            "2300200": {
                "codigo_municipio_tce": "004",
                "codigo_municipio_ibge": "2300200",
                "nome": "Acarau",
                "uf": "CE",
            },
            "2312908": {
                "codigo_municipio_tce": "162",
                "codigo_municipio_ibge": "2312908",
                "nome": "Sobral",
                "uf": "CE",
            },
        }
        localizar_municipio.side_effect = lambda codigo, uf: municipios[codigo]
        listar_publicacoes.side_effect = lambda **kwargs: {
            "2025-01" if kwargs["codigo_municipio_tce"] == "004" else "2024-01":
                datetime(2026, 10, 1, tzinfo=timezone.utc)
        }
        listar_empenhos.side_effect = lambda **kwargs: [
            _empenho("004", "2025-01-15", 10_000, "11111111000111")
            if kwargs["codigo_municipio_tce"] == "004"
            else _empenho("162", "2024-01-15", 5_000, "22222222000122")
        ]
        caches = {
            "11111111000111": _cache("11111111000111", "ME", "Acarau"),
            "22222222000122": _cache("22222222000122", "MEI", "Sobral"),
        }
        listar_cache.side_effect = lambda cnpjs: {
            cnpj: caches[cnpj] for cnpj in cnpjs if cnpj in caches
        }

        resultado = comparisons.consultar_comparacao_tce(_payload())

        self.assertEqual(resultado["origem_dados"], "BANCO_LOCAL")
        self.assertFalse(resultado["consultas_externas"])
        self.assertEqual(
            resultado["left"]["overview"]["kpis"]["total_compras_ME_centavos"],
            10_000,
        )
        self.assertEqual(
            resultado["right"]["overview"]["kpis"]["total_compras_ME_centavos"],
            5_000,
        )
        total_me = next(
            item
            for item in resultado["sections"]["kpis"]
            if item["indicator"] == "total_compras_ME_centavos"
        )
        self.assertEqual(total_me["ordering"], "LEFT_HIGHER")
        self.assertEqual(total_me["absolute_difference"], 5_000)
        obter_fornecedores.assert_not_called()
        atualizar_competencias.assert_not_called()

    @patch("app.pipeline.comparisons.tce_despesas_persistence.listar_empenhos_liquidos_publicados")
    @patch("app.pipeline.comparisons.tce_despesas_persistence.listar_publicacoes_competencias")
    @patch("app.pipeline.comparisons.database.localizar_municipio_tce_por_ibge")
    def test_competencia_publicada_sem_movimento_e_valida_e_retorna_zero(
        self,
        localizar_municipio,
        listar_publicacoes,
        listar_empenhos,
    ) -> None:
        localizar_municipio.return_value = {
            "codigo_municipio_tce": "010",
            "codigo_municipio_ibge": "2300754",
            "nome": "Amontada",
            "uf": "CE",
        }
        listar_publicacoes.return_value = {
            "2026-11": datetime(2026, 10, 1, tzinfo=timezone.utc)
        }
        listar_empenhos.return_value = []

        overview = comparisons._montar_overview_local(
            _filtros("2300754", "2026-11-01", "2026-11-30"),
            lado="left",
        )

        self.assertEqual(overview["competencias_publicadas"], ["2026-11"])
        self.assertEqual(overview["competencias_ausentes"], [])
        self.assertEqual(overview["totais_brutos"]["empenhos"], 0)
        self.assertEqual(overview["kpis"]["total_compras_ME_centavos"], 0)
        self.assertEqual(len(overview["elementos_despesa"]), 7)
        self.assertEqual(len(overview["destino_recursos"]), 4)
        secoes = comparisons._secoes(
            overview,
            overview,
            label_esquerdo="Amontada novembro",
            label_direito="Amontada novembro",
        )
        mensal = secoes["evolucao_compras_consideradas"][0]
        self.assertTrue(mensal["left_published"])
        self.assertEqual(
            mensal["microempresas_centavos"]["left"]["value"],
            0,
        )

    def test_rejeita_intervalo_invertido_com_erro_padronizavel(self) -> None:
        payload = _payload()
        payload["left"]["filters"]["start_date"] = "2025-12-31"
        payload["left"]["filters"]["end_date"] = "2025-01-01"

        with self.assertRaises(comparisons.ComparisonRequestError) as contexto:
            comparisons.consultar_comparacao_tce(payload)

        self.assertEqual(contexto.exception.code, "INVALID_DATE_RANGE")
        self.assertEqual(
            contexto.exception.details[0],
            {
                "field": "left.filters.start_date",
                "reason": "after_end_date",
                "value": "2025-12-31",
            },
        )

    def test_rejeita_modo_incompativel_com_os_lados(self) -> None:
        payload = _payload()
        payload["comparison_mode"] = "PERIODS"

        with self.assertRaises(comparisons.ComparisonRequestError) as contexto:
            comparisons.consultar_comparacao_tce(payload)

        self.assertEqual(contexto.exception.code, "COMPARISON_MODE_MISMATCH")

    def test_endpoint_retorna_envelope_de_erro_e_request_id(self) -> None:
        payload = _payload()
        payload["left"]["filters"]["start_date"] = "2025-12-31"
        payload["left"]["filters"]["end_date"] = "2025-01-01"
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/pipeline/tce/comparisons",
                "headers": [(b"x-request-id", b"req_teste_comparacao")],
            }
        )
        resposta = main.tce_comparisons(
            TceComparisonRequest.model_validate(payload),
            request,
            Response(),
        )

        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(resposta.headers["X-Request-ID"], "req_teste_comparacao")
        corpo = json.loads(resposta.body)
        self.assertEqual(corpo["error"]["code"], "INVALID_DATE_RANGE")
        self.assertEqual(corpo["error"]["request_id"], "req_teste_comparacao")

    def test_validacao_do_body_tambem_usa_envelope_padronizado(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/pipeline/tce/comparisons",
                "headers": [],
            }
        )
        error = RequestValidationError(
            [
                {
                    "type": "missing",
                    "loc": ("body", "left"),
                    "msg": "Field required",
                    "input": {},
                }
            ]
        )
        resposta = asyncio.run(main.validation_exception_handler(request, error))

        self.assertEqual(resposta.status_code, 422)
        corpo = json.loads(resposta.body)
        self.assertEqual(corpo["error"]["code"], "INVALID_REQUEST")
        self.assertTrue(corpo["error"]["request_id"].startswith("req_"))


if __name__ == "__main__":
    unittest.main()

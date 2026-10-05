from __future__ import annotations

import asyncio
import json
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

from fastapi import Request
from fastapi.exceptions import RequestValidationError

from app import main
from app.pipeline import empenhos, fornecedores


def _agregado() -> dict[str, object]:
    return {
        "tipo_documento_fornecedor": "1",
        "documento_fornecedor": "05537536000164",
        "cnpj_fornecedor": "05537536000164",
        "nome_fornecedor": "ECOFOR AMBIENTAL S/A",
        "porte_fornecedor": "DEMAIS",
        "cadastro_status": "DISPONIVEL",
        "origens_no_recorte": ["NO_MUNICIPIO_COMPRADOR"],
        "valor_empenhado_centavos": 100_000,
        "valor_anulado_centavos": 10_000,
        "valor_liquido_centavos": 90_000,
        "quantidade_empenhos": 2,
        "quantidade_anulacoes": 1,
        "quantidade_municipios": 1,
        "primeiro_empenho": "2025-01-10",
        "ultimo_empenho": "2025-02-10",
    }


def _resumo() -> dict[str, object]:
    return {
        **_agregado(),
        "quantidade_contratos": 1,
        "quantidade_licitacoes": 1,
        "meses_com_movimento": 2,
        "valor_total_recorte_centavos": 900_000,
        "cadastro": {
            "dados_normalizados": {
                "opencnpj_razao_social": "ECOFOR AMBIENTAL S/A",
                "opencnpj_nome_fantasia": "nao_informado",
                "opencnpj_situacao_cadastral": "Ativa",
                "opencnpj_capital_social": "1000.50",
                "opencnpj_opcao_simples": "Não",
                "municipio_sede": "FORTALEZA",
                "uf_sede": "CE",
                "cnae_principal_codigo": "3811400",
            },
            "observado_em": "2026-10-01T12:00:00+00:00",
            "cache_desatualizado": False,
            "ultima_tentativa_status": "ok",
        },
        "evolucao_mensal": [{
            "competencia": "2025-01",
            "valor_empenhado_centavos": 100_000,
            "valor_anulado_centavos": 10_000,
            "valor_liquido_centavos": 90_000,
            "quantidade_empenhos": 2,
        }],
        "por_elemento_despesa": [{
            "codigo_elemento_despesa": "39",
            "valor_empenhado_centavos": 100_000,
            "valor_anulado_centavos": 10_000,
            "valor_liquido_centavos": 90_000,
            "quantidade_empenhos": 2,
        }],
    }


def _empenho() -> dict[str, object]:
    return {
        "chave_empenho": "057|202500|27|101|2025-01-10|1",
        "exercicio_orcamento": 202500,
        "data_empenho": "2025-01-10",
        "numero_empenho": "1",
        "codigo_natureza_despesa": "33903900",
        "codigo_elemento_despesa": "39",
        "valor_empenhado_centavos": 100_000,
        "valor_anulado_centavos": 10_000,
        "valor_liquido_centavos": 90_000,
        "quantidade_anulacoes": 1,
        "documento_fornecedor": "05537536000164",
        "cnpj_fornecedor": "05537536000164",
        "nome_fornecedor": "ECOFOR AMBIENTAL S/A",
        "porte_fornecedor": "DEMAIS",
        "origem_fornecedor": "NO_MUNICIPIO_COMPRADOR",
        "codigo_municipio_tce": "057",
        "codigo_municipio_ibge": "2304400",
        "municipio_comprador": "Fortaleza",
        "uf_comprador": "CE",
    }


class FornecedoresTest(unittest.TestCase):
    @patch("app.pipeline.fornecedores.tce_fornecedores.listar_fornecedores_publicados_paginados")
    def test_listagem_formata_fornecedores_e_paginacao(self, listar) -> None:
        listar.return_value = {"total": 51, "registros": [_agregado()]}
        resultado = fornecedores.listar_fornecedores(
            start_date="2025-01-01",
            end_date="2025-12-31",
            uf="CE",
            municipality_tce_code=None,
            company_sizes=[],
            supplier_origins=[],
            expense_element_codes=[],
            search=None,
            page=1,
            page_size=50,
            sort_by="net_value",
            sort_order="desc",
        )
        self.assertEqual(resultado["items"][0]["tipo_documento"], "CNPJ")
        self.assertEqual(resultado["items"][0]["valores"]["valor_considerado_centavos"], 90_000)
        self.assertEqual(resultado["pagination"]["total_pages"], 2)

    @patch("app.pipeline.fornecedores.tce_fornecedores.obter_resumo_fornecedor_publicado")
    def test_detalhe_retorna_cadastro_graficos_e_participacao(self, obter) -> None:
        obter.return_value = _resumo()
        resultado = fornecedores.obter_fornecedor(
            tipo_documento="CNPJ",
            documento="05.537.536/0001-64",
            start_date="2025-01-01",
            end_date="2025-12-31",
            uf="CE",
            municipality_tce_code=None,
            company_sizes=[],
            supplier_origins=[],
            expense_element_codes=[],
        )
        cadastro = resultado["fornecedor"]["cadastro"]
        self.assertEqual(cadastro["capital_social_centavos"], 100_050)
        self.assertIsNone(cadastro["nome_fantasia"])
        self.assertFalse(cadastro["simples_nacional"])
        self.assertEqual(resultado["participacao_no_recorte"]["percentual"], 10.0)
        self.assertEqual(len(resultado["por_elemento_despesa"]), 7)
        self.assertNotIn("dados_normalizados", cadastro)

    @patch("app.pipeline.fornecedores.tce_despesas.listar_empenhos_publicados_paginados")
    def test_empenhos_do_fornecedor_reutilizam_formato_analitico(self, listar) -> None:
        listar.return_value = {"total": 1, "registros": [_empenho()]}
        resultado = fornecedores.listar_empenhos_fornecedor(
            tipo_documento="CNPJ",
            documento="05537536000164",
            start_date="2025-01-01",
            end_date="2025-12-31",
            uf="CE",
            municipality_tce_code=None,
            company_sizes=[],
            supplier_origins=[],
            expense_element_codes=[],
            search=None,
            page=1,
            page_size=20,
            sort_by="net_value",
            sort_order="desc",
        )
        self.assertEqual(resultado["items"][0]["display_id"], "EMP-2025-1")
        _, kwargs = listar.call_args
        self.assertEqual(kwargs["tipo_documento_fornecedor"], "1")
        self.assertEqual(kwargs["documento_fornecedor"], "05537536000164")

    def test_rejeita_documento_incompativel_com_tipo(self) -> None:
        with self.assertRaises(empenhos.EmpenhoRequestError) as contexto:
            fornecedores.obter_fornecedor(
                tipo_documento="CNPJ",
                documento="123",
                start_date="2025-01-01",
                end_date="2025-12-31",
                uf="CE",
                municipality_tce_code=None,
                company_sizes=[],
                supplier_origins=[],
                expense_element_codes=[],
            )
        self.assertEqual(contexto.exception.code, "INVALID_SUPPLIER_DOCUMENT")

    def test_validacao_da_rota_usa_envelope_padronizado(self) -> None:
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/pipeline/tce/fornecedores",
            "headers": [],
        })
        error = RequestValidationError([{
            "type": "greater_than_equal",
            "loc": ("query", "page"),
            "msg": "Input should be greater than or equal to 1",
            "input": 0,
        }])
        resposta = asyncio.run(main.validation_exception_handler(request, error))
        corpo = json.loads(resposta.body)
        self.assertEqual(corpo["error"]["code"], "INVALID_PAGE")


if __name__ == "__main__":
    unittest.main()

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
from app.pipeline import empenhos


def _registro() -> dict[str, object]:
    return {
        "chave_empenho": "057|202500|27|101|2025-07-24|27000294",
        "exercicio_orcamento": 202500,
        "data_empenho": "2025-07-24",
        "numero_empenho": "27000294",
        "codigo_natureza_despesa": "44903900",
        "codigo_elemento_despesa": "39",
        "valor_empenhado_centavos": 5_000_000_000,
        "valor_anulado_centavos": 2_211_858_998,
        "valor_liquido_centavos": 2_788_141_002,
        "quantidade_anulacoes": 2,
        "documento_fornecedor": "50809245000132",
        "cnpj_fornecedor": "50809245000132",
        "cpf_fornecedor": None,
        "nome_fornecedor": "CONSORCIO PAVFOR",
        "tipo_documento_fornecedor": "1",
        "porte_fornecedor": "DEMAIS",
        "origem_fornecedor": "NO_MUNICIPIO_COMPRADOR",
        "municipio_fornecedor": "FORTALEZA",
        "uf_fornecedor": "CE",
        "codigo_municipio_tce": "057",
        "codigo_municipio_ibge": "2304400",
        "municipio_comprador": "Fortaleza",
        "uf_comprador": "CE",
        "competencia": "2025-07",
        "codigo_orgao": "27",
        "codigo_unidade_orcamentaria": "101",
        "cnae_principal_codigo": "4213800",
        "cnae_principal_descricao": "Obras",
        "fornecedor_observado_em": "2026-10-01T19:43:53+00:00",
        "estado_empenho": "AP",
        "numero_contrato": "2024007474",
        "numero_licitacao": "20232749002450",
        "numero_empenho_substituto": None,
        "numero_nota_anulacao_informado": None,
        "run_id": 556,
        "status_publicacao": "PUBLICADO",
        "publicado_em": "2026-10-01T19:39:22+00:00",
    }


class EmpenhosTest(unittest.TestCase):
    @patch("app.pipeline.empenhos.persistence.listar_empenhos_publicados_paginados")
    @patch("app.pipeline.empenhos.database.localizar_municipio_tce")
    def test_lista_aplica_filtros_e_formata_paginacao(self, localizar, listar) -> None:
        localizar.return_value = {
            "codigo_municipio_tce": "057",
            "codigo_municipio_ibge": "2304400",
            "nome": "Fortaleza",
            "uf": "CE",
        }
        listar.return_value = {"total": 51, "registros": [_registro()]}

        resultado = empenhos.listar_empenhos(
            start_date="2025-01-01",
            end_date="2025-12-31",
            uf="CE",
            municipality_tce_code="057",
            company_sizes=["OTHER"],
            supplier_origins=["NO_MUNICIPIO_COMPRADOR"],
            expense_element_codes=["39"],
            search="EMP-2025-27000294",
            page=2,
            page_size=50,
            sort_by="net_value",
            sort_order="desc",
        )

        self.assertEqual(resultado["items"][0]["display_id"], "EMP-2025-27000294")
        self.assertEqual(resultado["items"][0]["valores"]["valor_liquido_centavos"], 2_788_141_002)
        self.assertEqual(resultado["pagination"]["total_pages"], 2)
        self.assertFalse(resultado["pagination"]["has_next"])
        _, kwargs = listar.call_args
        self.assertEqual(kwargs["codigo_municipio_tce"], "057")
        self.assertEqual(kwargs["portes"], ["DEMAIS"])
        self.assertEqual(kwargs["busca"], "27000294")

    def test_rejeita_intervalo_invertido(self) -> None:
        with self.assertRaises(empenhos.EmpenhoRequestError) as contexto:
            empenhos.listar_empenhos(
                start_date="2025-12-31",
                end_date="2025-01-01",
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
        self.assertEqual(contexto.exception.code, "INVALID_DATE_RANGE")

    @patch("app.pipeline.empenhos.persistence.listar_anulacoes_publicadas_por_empenho")
    @patch("app.pipeline.empenhos.persistence.obter_empenho_publicado_por_chave")
    def test_detalhe_retorna_valores_e_anulacoes(self, obter, listar_anulacoes) -> None:
        obter.return_value = _registro()
        listar_anulacoes.return_value = [{
            "chave_anulacao": "anulacao-1",
            "numero_anulacao": "49",
            "data_anulacao": "2025-08-12",
            "modalidade_anulacao": "P",
            "descricao_anulacao": "Anulacao parcial",
            "valor_anulacao_centavos": 2_211_858_998,
        }]

        resultado = empenhos.obter_empenho(str(_registro()["chave_empenho"]))

        self.assertEqual(resultado["classificacao_orcamentaria"]["exercicio"], 2025)
        self.assertEqual(resultado["publicacao"]["status"], "PUBLICADO")
        self.assertEqual(len(resultado["anulacoes"]), 1)

    def test_nao_encontrado_retorna_404_logico(self) -> None:
        with patch(
            "app.pipeline.empenhos.persistence.obter_empenho_publicado_por_chave",
            return_value=None,
        ):
            with self.assertRaises(empenhos.EmpenhoRequestError) as contexto:
                empenhos.obter_empenho("inexistente")
        self.assertEqual(contexto.exception.code, "COMMITMENT_NOT_FOUND")
        self.assertEqual(contexto.exception.status_code, 404)

    def test_validacao_de_query_usa_envelope_padronizado(self) -> None:
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/pipeline/tce/empenhos",
            "headers": [(b"x-request-id", b"req_empenhos")],
        })
        error = RequestValidationError([{
            "type": "greater_than_equal",
            "loc": ("query", "page"),
            "msg": "Input should be greater than or equal to 1",
            "input": 0,
        }])
        resposta = asyncio.run(main.validation_exception_handler(request, error))
        corpo = json.loads(resposta.body)

        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(corpo["error"]["code"], "INVALID_PAGE")
        self.assertEqual(corpo["error"]["details"][0]["field"], "page")
        self.assertEqual(corpo["error"]["request_id"], "req_empenhos")


if __name__ == "__main__":
    unittest.main()

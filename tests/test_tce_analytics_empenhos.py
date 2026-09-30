from __future__ import annotations

from types import SimpleNamespace
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import call, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from app.pipeline import analisys


def _publicado():
    return SimpleNamespace(status="PUBLICADO", problemas=[])


def _empenho(
    numero: str,
    data_empenho: str,
    natureza: str,
    valor_empenhado: int,
    valor_anulado: int,
    cnpj: str,
    nome: str,
) -> dict[str, object]:
    return {
        "codigo_municipio_tce": "010",
        "chave_empenho": f"010|202500|17|01|{data_empenho}|{numero}",
        "exercicio_orcamento": 202500,
        "codigo_orgao": "17",
        "codigo_unidade_orcamentaria": "01",
        "data_empenho": data_empenho,
        "numero_empenho": numero,
        "codigo_natureza_despesa": f"3390{natureza}00",
        "codigo_elemento_despesa": natureza,
        "valor_empenhado_centavos": valor_empenhado,
        "valor_anulado_centavos": valor_anulado,
        "valor_liquido_centavos": valor_empenhado - valor_anulado,
        "tipo_documento_fornecedor": "1",
        "documento_fornecedor": cnpj,
        "cnpj_fornecedor": cnpj,
        "cpf_fornecedor": None,
        "nome_fornecedor": nome,
        "municipio_fornecedor_informado": None,
        "uf_fornecedor_informada": None,
        "estado_empenho": "OR",
    }


FORNECEDORES = [
    {
        "cnpj": "11444777000161",
        "razao_social": "Comercio Local LTDA",
        "porte": "Microempresa (ME)",
        "opencnpj": {
            "porte_empresa": "Microempresa (ME)",
            "municipio": "AMONTADA",
            "uf": "CE",
            "cnae_principal": "4711302",
            "simples_mei": {"opcao_mei": "N"},
        },
        "opencnpj_status": "ok",
    },
    {
        "cnpj": "98765432000111",
        "razao_social": "Fornecedor Fortaleza SA",
        "porte": "Empresa de Pequeno Porte (EPP)",
        "opencnpj": {
            "porte_empresa": "Empresa de Pequeno Porte (EPP)",
            "municipio": "FORTALEZA",
            "uf": "CE",
            "cnae_principal": "6201501",
        },
        "opencnpj_status": "ok",
    },
]


class BaseAnaliticaEmpenhosTest(unittest.TestCase):
    @patch("app.pipeline.analisys.fornecedores.coletar_fornecedores_em_lote")
    @patch("app.pipeline.analisys.tce_despesas_persistence.listar_empenhos_liquidos_publicados")
    @patch("app.pipeline.analisys.tce_despesas_pipeline.executar_ingestao_competencia_tce")
    def test_coleta_competencias_e_calcula_sobre_valor_liquido(
        self,
        executar_ingestao,
        listar_empenhos,
        coletar_fornecedores,
    ) -> None:
        executar_ingestao.side_effect = [_publicado(), _publicado()]
        listar_empenhos.return_value = [
            _empenho(
                "31010002",
                "2025-01-31",
                "30",
                1_000_000,
                100_000,
                "11444777000161",
                "Comercio Local LTDA",
            ),
            _empenho(
                "28020001",
                "2025-02-28",
                "39",
                2_000_000,
                500_000,
                "98765432000111",
                "Fornecedor Fortaleza SA",
            ),
        ]
        coletar_fornecedores.return_value = FORNECEDORES

        base, metadados = analisys.montar_base_analitica_tce(
            data_inicial="2025-01-15",
            data_final="2025-02-28",
            codigo_municipio="010",
            municipio_comprador="Amontada",
            throttle_fornecedores=0,
        )

        self.assertEqual(
            executar_ingestao.call_args_list,
            [
                call("2025-01", codigo_municipio_tce="010"),
                call("2025-02", codigo_municipio_tce="010"),
            ],
        )
        self.assertEqual(metadados["competencias_ausentes"], [])
        self.assertEqual(metadados["base_analitica"]["nome"], "tce_empenhos_liquidos")
        self.assertEqual(metadados["base_analitica"]["base_calculo"], "empenhos_tce")
        self.assertEqual(metadados["totais_brutos"], {"empenhos": 2})

        janeiro = base.loc[base["numero_empenho"] == "31010002"].iloc[0]
        self.assertEqual(janeiro["base_calculo"], "empenhos_tce")
        self.assertEqual(janeiro["valor_empenhado"], 10_000.0)
        self.assertEqual(janeiro["valor_anulado"], 1_000.0)
        self.assertEqual(janeiro["valor"], 9_000.0)
        self.assertEqual(janeiro["porte_fornecedor"], "ME")
        self.assertFalse(janeiro["optante_mei"])
        self.assertTrue(janeiro["mei_discriminado"])
        self.assertEqual(janeiro["fonte_porte"], "RECEITA_FEDERAL_VIA_OPENCNPJ")
        self.assertEqual(janeiro["procedencia_porte"], "RETRATO_ATUAL")
        self.assertIsNotNone(janeiro["observado_em"])
        self.assertEqual(janeiro["origem_geografica"], "Sediado no município comprador")

    @patch("app.pipeline.analisys.fornecedores.coletar_fornecedores_em_lote", return_value=[])
    @patch("app.pipeline.analisys.tce_despesas_persistence.listar_empenhos_liquidos_publicados")
    @patch("app.pipeline.analisys.tce_despesas_pipeline.executar_ingestao_competencia_tce")
    def test_falha_parcial_sinaliza_e_exclui_competencia_ausente(
        self,
        executar_ingestao,
        listar_empenhos,
        coletar_fornecedores,
    ) -> None:
        executar_ingestao.side_effect = [_publicado(), RuntimeError("fevereiro indisponível")]
        listar_empenhos.return_value = [
            _empenho("J1", "2025-01-20", "30", 10_000, 0, "", "Janeiro"),
            _empenho("F1", "2025-02-20", "30", 20_000, 0, "", "Fevereiro"),
        ]

        base, metadados = analisys.montar_base_analitica_tce(
            data_inicial="2025-01-01",
            data_final="2025-02-28",
            codigo_municipio="010",
            municipio_comprador="Amontada",
            throttle_fornecedores=0,
        )

        self.assertEqual(base["numero_empenho"].tolist(), ["J1"])
        self.assertEqual(metadados["competencias_publicadas"], ["2025-01"])
        self.assertEqual(metadados["competencias_ausentes"], ["2025-02"])
        self.assertEqual(metadados["falhas_competencias"][0]["erro"], "fevereiro indisponível")

    @patch("app.pipeline.analisys.tce_despesas_pipeline.executar_ingestao_competencia_tce")
    def test_falha_em_todas_as_competencias_interrompe_com_erro_503_de_dominio(
        self,
        executar_ingestao,
    ) -> None:
        executar_ingestao.side_effect = RuntimeError("TCE indisponível")

        with self.assertRaises(analisys.CompetenciasTceIndisponiveisError):
            analisys.montar_base_analitica_tce(
                data_inicial="2025-01-01",
                data_final="2025-02-28",
                codigo_municipio="010",
                municipio_comprador="Amontada",
            )


class IndicadoresEmpenhosTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = None

    @patch(
        "app.pipeline.analisys.tce.buscar_municipios",
        return_value=[{"codigo_municipio": "010", "nome_municipio": "Amontada"}],
    )
    @patch("app.pipeline.analisys.fornecedores.coletar_fornecedores_em_lote")
    @patch("app.pipeline.analisys.tce_despesas_persistence.listar_empenhos_liquidos_publicados")
    @patch("app.pipeline.analisys.tce_despesas_pipeline.executar_ingestao_competencia_tce")
    def test_indicadores_e_porte_mensal_usam_empenhos_liquidos(
        self,
        executar_ingestao,
        listar_empenhos,
        coletar_fornecedores,
        buscar_municipios,
    ) -> None:
        executar_ingestao.return_value = _publicado()
        listar_empenhos.return_value = [
            _empenho("E1", "2025-01-10", "30", 1_000_000, 200_000, "11444777000161", "ME"),
            _empenho("E2", "2025-01-20", "39", 2_000_000, 500_000, "98765432000111", "EPP"),
        ]
        coletar_fornecedores.return_value = FORNECEDORES

        indicadores = analisys.consultar_tce_indicadores_analiticos(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
            municipio_comprador="Amontada",
            throttle_fornecedores=0,
        )
        portes = analisys.consultar_kpi_tce_portes_por_mes(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
            throttle_fornecedores=0,
        )

        resumo = indicadores["indicadores"]["resumo_geral"][0]
        self.assertEqual(resumo["valor_total"], 23_000.0)
        self.assertEqual(resumo["valor_me"], 8_000.0)
        self.assertEqual(resumo["valor_mpe"], 23_000.0)
        self.assertTrue(resumo["mei_discriminado"])
        self.assertEqual(resumo["estado_mpe"], "DISPONIVEL")
        self.assertIsNone(resumo["motivo_mpe"])
        self.assertEqual(portes["totais"]["empenhos"], 2)
        linha_me = next(linha for linha in portes["dados"] if linha["porte"] == "ME")
        self.assertEqual(linha_me["total_empenhos"], 2)
        self.assertEqual(linha_me["quantidade_empenhos"], 1)
        self.assertEqual(linha_me["valor_porte"], 8_000.0)
        self.assertNotIn("total_contratos", linha_me)

    @patch("app.pipeline.analisys.montar_base_analitica_tce")
    def test_overview_preserva_metadados_e_aplica_escopo_me_mei(
        self,
        montar_base,
    ) -> None:
        montar_base.return_value = (
            pd.DataFrame(
                [
                    {
                        "ano_mes": "2025-01",
                        "valor": 80.0,
                        "porte_fornecedor": "ME",
                        "origem_geografica": "Sediado no município comprador",
                        "natureza_despesa_codigo": "30",
                    },
                    {
                        "ano_mes": "2025-01",
                        "valor": 20.0,
                        "porte_fornecedor": "MEI",
                        "origem_geografica": "Outro município do Ceará",
                        "natureza_despesa_codigo": "39",
                    },
                ]
            ),
            {
                "fonte": "TCE-CE",
                "competencias_publicadas": ["2025-01"],
                "competencias_ausentes": [],
            },
        )

        resultado = analisys.consultar_overview_tce(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
            throttle_fornecedores=0,
        )

        montar_base.assert_called_once_with(
            "2025-01-01",
            "2025-01-31",
            codigo_municipio="010",
            throttle_fornecedores=0,
        )
        self.assertEqual(resultado["competencias_publicadas"], ["2025-01"])
        self.assertEqual(resultado["escopo"]["portes_considerados"], ["ME", "MEI"])
        self.assertEqual(
            resultado["escopo"]["portes_no_denominador_participacao"],
            ["ME", "MEI", "EPP", "DEMAIS"],
        )
        self.assertEqual(resultado["unidade_monetaria"], "CENTAVOS")
        self.assertEqual(resultado["kpis"]["percentual_participacao_me"], 100.0)
        self.assertEqual(
            resultado["kpis"]["total_compras_ME_centavos"],
            10_000,
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

import pandas as pd

from app.pipeline import kpis


class ExtrairAnoMesTest(unittest.TestCase):
    def test_extrai_de_data_e_de_datetime_com_z(self) -> None:
        serie = pd.Series(["2025-01-15", "2025-02-10T09:00:00Z", "2025-03-01T09:00:00", None, "nao_informado"])

        resultado = kpis.extrair_ano_mes(serie)

        self.assertEqual(
            resultado.tolist()[:3],
            ["2025-01", "2025-02", "2025-03"],
        )
        self.assertTrue(resultado.isna().tolist()[3:] == [True, True])


class CalcularIndicadoresAnaliticosPrincipaisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = pd.DataFrame(
            [
                {
                    "ano_mes": "2025-01",
                    "valor": 10000.0,
                    "cnpj_fornecedor": "11444777000161",
                    "nome_fornecedor": "Comercio Local LTDA",
                    "porte_fornecedor": "ME",
                    "fornecedor_e_me": True,
                    "fornecedor_e_mpe": True,
                    "natureza_despesa_codigo": "30",
                    "natureza_despesa": "Material de consumo",
                    "origem_geografica": "Sediado no município comprador",
                    "municipio_sede_fornecedor": "AMONTADA",
                    "uf_sede_fornecedor": "CE",
                },
                {
                    "ano_mes": "2025-01",
                    "valor": 20000.0,
                    "cnpj_fornecedor": "98765432000111",
                    "nome_fornecedor": "Fornecedor EPP SA",
                    "porte_fornecedor": "EPP",
                    "fornecedor_e_me": False,
                    "fornecedor_e_mpe": True,
                    "natureza_despesa_codigo": "39",
                    "natureza_despesa": "Outros servicos de terceiros - pessoa juridica",
                    "origem_geografica": "Outro município do Ceará",
                    "municipio_sede_fornecedor": "FORTALEZA",
                    "uf_sede_fornecedor": "CE",
                },
                {
                    "ano_mes": "2025-02",
                    "valor": 5000.0,
                    "cnpj_fornecedor": "55555555000155",
                    "nome_fornecedor": "Fornecedor Nacional SA",
                    "porte_fornecedor": "DEMAIS",
                    "fornecedor_e_me": False,
                    "fornecedor_e_mpe": False,
                    "natureza_despesa_codigo": "30",
                    "natureza_despesa": "Material de consumo",
                    "origem_geografica": "Fora do estado",
                    "municipio_sede_fornecedor": "SAO PAULO",
                    "uf_sede_fornecedor": "SP",
                },
                {
                    "ano_mes": "2025-02",
                    "valor": None,
                    "cnpj_fornecedor": None,
                    "nome_fornecedor": "Fornecedor sem valor",
                    "porte_fornecedor": "NAO_IDENTIFICADO",
                    "fornecedor_e_me": False,
                    "fornecedor_e_mpe": False,
                    "natureza_despesa_codigo": "30",
                    "natureza_despesa": "Material de consumo",
                    "origem_geografica": "Nao identificada",
                    "municipio_sede_fornecedor": None,
                    "uf_sede_fornecedor": None,
                },
            ]
        )

    def test_calcula_resumo_series_distribuicoes_e_ranking(self) -> None:
        indicadores = kpis.calcular_indicadores_analiticos_principais(self.base, limite_ranking=2)

        self.assertEqual(set(indicadores), set(kpis.INDICADORES_ANALITICOS_PRINCIPAIS))

        resumo = indicadores["resumo_geral"].iloc[0]
        self.assertEqual(resumo["total_registros"], 4)
        self.assertEqual(resumo["total_fornecedores"], 3)
        self.assertEqual(resumo["registros_sem_valor"], 1)
        self.assertEqual(resumo["valor_total"], 35000.0)
        self.assertEqual(resumo["valor_me"], 10000.0)
        self.assertAlmostEqual(resumo["percentual_me"], 10000.0 / 35000.0)
        self.assertEqual(resumo["valor_mpe"], 30000.0)
        self.assertAlmostEqual(resumo["percentual_mpe"], 30000.0 / 35000.0)
        self.assertEqual(resumo["valor_fornecedor_local"], 10000.0)

        serie = indicadores["serie_mensal"].set_index("ano_mes")
        self.assertEqual(serie.loc["2025-01", "valor_total"], 30000.0)
        self.assertEqual(serie.loc["2025-01", "valor_mpe"], 30000.0)
        self.assertEqual(serie.loc["2025-02", "valor_total"], 5000.0)

        naturezas = indicadores["por_natureza_despesa"].set_index("natureza_despesa_codigo")
        self.assertEqual(naturezas.loc["30", "registros"], 3)
        self.assertEqual(naturezas.loc["30", "valor_total"], 15000.0)

        ranking = indicadores["ranking_fornecedores"]
        self.assertEqual(len(ranking), 2)
        self.assertEqual(ranking.iloc[0]["cnpj_fornecedor"], "98765432000111")
        self.assertEqual(ranking.iloc[0]["valor_total"], 20000.0)

    def test_recusa_base_sem_colunas_canonicas(self) -> None:
        with self.assertRaisesRegex(kpis.DadosInsuficientesKPI, "base analitica"):
            kpis.calcular_indicadores_analiticos_principais(pd.DataFrame([{"valor": 100.0}]))

    def test_recusa_limite_ranking_invalido(self) -> None:
        with self.assertRaisesRegex(ValueError, "limite_ranking"):
            kpis.calcular_indicadores_analiticos_principais(self.base, limite_ranking=0)

    def test_mpe_fica_indisponivel_quando_mei_nao_foi_discriminado(self) -> None:
        base = self.base.copy()
        base["fornecedor_e_mpe"] = base["fornecedor_e_mpe"].astype("boolean")
        base.loc[base["porte_fornecedor"].eq("ME"), "fornecedor_e_mpe"] = pd.NA

        indicadores = kpis.calcular_indicadores_analiticos_principais(base)

        resumo = indicadores["resumo_geral"].iloc[0]
        self.assertEqual(resumo["valor_total"], 35000.0)
        self.assertEqual(resumo["valor_me"], 10000.0)
        self.assertTrue(pd.isna(resumo["valor_mpe"]))
        self.assertTrue(pd.isna(resumo["percentual_mpe"]))
        self.assertFalse(resumo["mei_discriminado"])
        self.assertEqual(resumo["estado_mpe"], "INDISPONIVEL")
        self.assertEqual(resumo["motivo_mpe"], "MEI_NAO_DISCRIMINADO")

        serie = indicadores["serie_mensal"].set_index("ano_mes")
        self.assertTrue(pd.isna(serie.loc["2025-01", "valor_mpe"]))
        self.assertFalse(serie.loc["2025-01", "mei_discriminado"])
        self.assertEqual(serie.loc["2025-01", "estado_mpe"], "INDISPONIVEL")
        self.assertEqual(serie.loc["2025-01", "motivo_mpe"], "MEI_NAO_DISCRIMINADO")
        self.assertEqual(serie.loc["2025-02", "valor_mpe"], 0.0)
        self.assertTrue(serie.loc["2025-02", "mei_discriminado"])
        self.assertEqual(serie.loc["2025-02", "estado_mpe"], "DISPONIVEL")


class CalcularOverviewMeMeiTest(unittest.TestCase):
    def test_restringe_escopo_e_calcula_kpis_e_series_em_centavos(self) -> None:
        base = pd.DataFrame(
            [
                {
                    "ano_mes": "2025-01",
                    "valor": 100.0,
                    "porte_fornecedor": "ME",
                    "origem_geografica": "Sediado no município comprador",
                },
                {
                    "ano_mes": "2025-01",
                    "valor": 50.0,
                    "porte_fornecedor": "MEI",
                    "origem_geografica": "Outro município do Ceará",
                },
                {
                    "ano_mes": "2025-02",
                    "valor": 30.0,
                    "porte_fornecedor": "ME",
                    "origem_geografica": "Fora do estado",
                },
                {
                    "ano_mes": "2025-02",
                    "valor": 20.0,
                    "porte_fornecedor": "MEI",
                    "origem_geografica": "Nao identificada",
                },
                {
                    "ano_mes": "2025-03",
                    "valor": 500.0,
                    "porte_fornecedor": "EPP",
                    "origem_geografica": "Sediado no município comprador",
                },
                {
                    "ano_mes": "2025-04",
                    "valor": 800.0,
                    "porte_fornecedor": "NAO_IDENTIFICADO",
                    "origem_geografica": "Nao identificada",
                },
            ]
        )
        base["natureza_despesa_codigo"] = ["30", "30", "39", "39", "51", "52"]

        resultado = kpis.calcular_overview_me_mei(base)

        self.assertAlmostEqual(
            resultado["kpis"]["percentual_participacao_me"],
            200 / 700 * 100,
        )
        self.assertEqual(
            resultado["kpis"]["total_compras_ME_centavos"],
            20_000,
        )
        self.assertEqual(
            resultado["kpis"]["percentual_compras_fornecedores_locais"],
            50.0,
        )
        self.assertEqual(
            resultado["kpis"]["percentual_recursos_fora_municipio"],
            40.0,
        )
        participacao = resultado["participacao_por_porte_empresarial"]
        self.assertEqual(
            [item["porte"] for item in participacao],
            ["ME", "MEI", "OUTROS_PORTES", "NAO_IDENTIFICADO"],
        )
        self.assertEqual(
            [item["valor_centavos"] for item in participacao],
            [13_000, 7_000, 50_000, 80_000],
        )
        self.assertAlmostEqual(
            sum(item["percentual"] for item in participacao),
            100.0,
        )
        elementos = resultado["elementos_despesa"]
        self.assertEqual(
            [item["codigo"] for item in elementos],
            ["30", "32", "35", "39", "40", "51", "52"],
        )
        self.assertEqual(
            {item["codigo"]: item["valor_liquido_centavos"] for item in elementos},
            {
                "30": 15_000,
                "32": 0,
                "35": 0,
                "39": 5_000,
                "40": 0,
                "51": 0,
                "52": 0,
            },
        )
        self.assertEqual(elementos[0]["nome"], "Material de consumo")
        self.assertEqual(
            resultado["evolucao_compras_consideradas"],
            [
                {
                    "periodo": "2025-01",
                    "compras_consideradas_centavos": 15_000,
                    "microempresas_centavos": 15_000,
                },
                {
                    "periodo": "2025-02",
                    "compras_consideradas_centavos": 5_000,
                    "microempresas_centavos": 5_000,
                },
                {
                    "periodo": "2025-03",
                    "compras_consideradas_centavos": 50_000,
                    "microempresas_centavos": 0,
                },
            ],
        )
        self.assertEqual(
            resultado["destino_recursos"],
            [
                {
                    "destino": "NO_MUNICIPIO_COMPRADOR",
                    "valor_centavos": 10_000,
                    "percentual": 50.0,
                },
                {
                    "destino": "EM_OUTRO_MUNICIPIO",
                    "valor_centavos": 5_000,
                    "percentual": 25.0,
                },
                {
                    "destino": "FORA_DO_CEARA",
                    "valor_centavos": 3_000,
                    "percentual": 15.0,
                },
                {
                    "destino": "ORIGEM_NAO_IDENTIFICADA",
                    "valor_centavos": 2_000,
                    "percentual": 10.0,
                },
            ],
        )

    def test_periodo_so_com_epp_mantem_denominador_sem_total_me(self) -> None:
        base = pd.DataFrame(
            [
                {
                    "ano_mes": "2025-01",
                    "valor": 100.0,
                    "porte_fornecedor": "EPP",
                    "origem_geografica": "Sediado no município comprador",
                }
            ]
        )
        base["natureza_despesa_codigo"] = "30"

        resultado = kpis.calcular_overview_me_mei(base)

        self.assertEqual(
            resultado["kpis"],
            {
                "percentual_participacao_me": 0.0,
                "total_compras_ME_centavos": 0,
                "percentual_compras_fornecedores_locais": 0.0,
                "percentual_recursos_fora_municipio": 0.0,
            },
        )
        self.assertEqual(
            resultado["evolucao_compras_consideradas"],
            [
                {
                    "periodo": "2025-01",
                    "compras_consideradas_centavos": 10_000,
                    "microempresas_centavos": 0,
                }
            ],
        )
        self.assertTrue(
            all(item["valor_centavos"] == 0 for item in resultado["destino_recursos"])
        )
        self.assertTrue(
            all(item["percentual"] == 0.0 for item in resultado["destino_recursos"])
        )

    def test_periodo_sem_porte_identificado_retorna_zeros(self) -> None:
        base = pd.DataFrame(
            [
                {
                    "ano_mes": "2025-01",
                    "valor": 100.0,
                    "porte_fornecedor": "NAO_IDENTIFICADO",
                    "origem_geografica": "Nao identificada",
                }
            ]
        )
        base["natureza_despesa_codigo"] = "30"

        resultado = kpis.calcular_overview_me_mei(base)

        self.assertEqual(resultado["kpis"]["total_compras_ME_centavos"], 0)
        self.assertEqual(resultado["kpis"]["percentual_participacao_me"], 0.0)
        self.assertEqual(resultado["evolucao_compras_consideradas"], [])
        self.assertEqual(
            resultado["participacao_por_porte_empresarial"][-1],
            {
                "porte": "NAO_IDENTIFICADO",
                "valor_centavos": 10_000,
                "percentual": 100.0,
            },
        )

    def test_participacao_por_porte_agrega_epp_e_demais(self) -> None:
        base = pd.DataFrame(
            [
                {
                    "ano_mes": "2025-01",
                    "valor": valor,
                    "porte_fornecedor": porte,
                    "origem_geografica": "Nao identificada",
                }
                for porte, valor in (
                    ("ME", 40.0),
                    ("MEI", 10.0),
                    ("EPP", 20.0),
                    ("DEMAIS", 10.0),
                    ("NAO_IDENTIFICADO", 20.0),
                )
            ]
        )
        base["natureza_despesa_codigo"] = "30"

        resultado = kpis.calcular_overview_me_mei(base)

        self.assertEqual(
            resultado["participacao_por_porte_empresarial"],
            [
                {"porte": "ME", "valor_centavos": 4_000, "percentual": 40.0},
                {"porte": "MEI", "valor_centavos": 1_000, "percentual": 10.0},
                {
                    "porte": "OUTROS_PORTES",
                    "valor_centavos": 3_000,
                    "percentual": 30.0,
                },
                {
                    "porte": "NAO_IDENTIFICADO",
                    "valor_centavos": 2_000,
                    "percentual": 20.0,
                },
            ],
        )


    def test_overview_por_portes_expoe_todos_por_padrao_e_filtra_calculos(self) -> None:
        base = pd.DataFrame(
            [
                {
                    "ano_mes": "2025-01",
                    "valor": valor,
                    "porte_fornecedor": porte,
                    "origem_geografica": origem,
                    "natureza_despesa_codigo": "30",
                }
                for porte, valor, origem in (
                    ("MEI", 10.0, kpis.ORIGEM_FORNECEDOR_LOCAL),
                    ("ME", 20.0, kpis.ORIGEM_FORNECEDOR_LOCAL),
                    ("EPP", 30.0, kpis.ORIGEM_FORNECEDOR_OUTRO_MUNICIPIO_CE),
                    ("DEMAIS", 40.0, "Fora do estado"),
                    ("NAO_IDENTIFICADO", 50.0, "Nao identificada"),
                )
            ]
        )

        todos = kpis.calcular_overview_portes(
            base,
            portes_considerados=list(kpis.PORTES_EMPRESARIAIS),
        )
        me_mei = kpis.calcular_overview_portes(
            base,
            portes_considerados=["ME", "MEI"],
        )

        self.assertEqual(
            [item["porte"] for item in todos["participacao_por_porte_empresarial"]],
            ["MEI", "ME", "EPP", "DEMAIS", "NAO_IDENTIFICADO"],
        )
        self.assertEqual(todos["kpis"]["total_compras_centavos"], 15_000)
        self.assertEqual(todos["kpis"]["total_compras_ME_centavos"], 3_000)
        self.assertEqual(todos["kpis"]["percentual_participacao_me"], 30.0)
        self.assertEqual(me_mei["kpis"]["total_compras_centavos"], 3_000)
        self.assertEqual(
            me_mei["kpis"]["percentual_compras_fornecedores_locais"],
            100.0,
        )
        self.assertEqual(
            me_mei["evolucao_compras_consideradas"][0][
                "compras_consideradas_centavos"
            ],
            3_000,
        )
        self.assertEqual(
            todos["evolucao_compras_consideradas"],
            [
                {
                    "periodo": "2025-01",
                    "compras_consideradas_centavos": 10_000,
                    "microempresas_centavos": 3_000,
                }
            ],
        )
        self.assertEqual(
            todos["evolucao_compras_por_portes"][0][
                "compras_consideradas_centavos"
            ],
            15_000,
        )


class CalcularParticipacaoMeLocalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.licitacoes = pd.DataFrame([
            {"id": "L1", "municipio_comprador": "São Gonçalo do Amarante", "secretaria": "Saúde", "data": "2025-01-10"},
            {"id": "L2", "municipio_comprador": "São Gonçalo do Amarante", "secretaria": "Educação", "data": "2025-02-10"},
            {"id": "L3", "municipio_comprador": "São Gonçalo do Amarante", "secretaria": "Saúde", "data": "2026-01-10"},
        ])

    def test_conta_licitacao_uma_vez_e_exclui_rotulos_nao_me(self) -> None:
        participantes = pd.DataFrame([
            {"licitacao": "L1", "cnpj": "11.444.777/0001-61", "porte": "ME", "municipio_empresa": "SAO GONCALO DO AMARANTE"},
            {"licitacao": "L1", "cnpj": "12.345.678/0001-00", "porte": "MICRO EMPRESA", "municipio_empresa": "São Gonçalo do Amarante"},
            {"licitacao": "L2", "cnpj": "22.222.222/0001-22", "porte": "EPP", "municipio_empresa": "São Gonçalo do Amarante"},
            {"licitacao": "L2", "cnpj": "33.333.333/0001-33", "porte": "ME/EPP", "municipio_empresa": "São Gonçalo do Amarante"},
            {"licitacao": "L3", "cnpj": "44.444.444/0001-44", "porte": "ME", "municipio_empresa": "Fortaleza"},
        ])

        resultado = kpis.calcular_participacao_me_local(
            self.licitacoes,
            participantes,
            coluna_licitacao="id",
            coluna_licitacao_participante="licitacao",
            coluna_porte="porte",
            coluna_municipio_empresa="municipio_empresa",
            coluna_municipio_comprador="municipio_comprador",
            coluna_cnpj="cnpj",
            coluna_secretaria="secretaria",
            coluna_data="data",
        )
        resumo = resultado["resumo_geral"].iloc[0]
        self.assertEqual(resumo["total_licitacoes"], 3)
        self.assertEqual(resumo["licitacoes_com_me_local"], 1)
        self.assertEqual(resumo["licitacoes_sem_me_local"], 2)
        self.assertAlmostEqual(resumo["percentual_me_local"], 100 / 3)
        self.assertEqual(resumo["licitacoes_com_me_externa"], 1)
        self.assertEqual(len(resultado["por_secretaria"]), 2)
        self.assertEqual(len(resultado["historico"]), 2)

    def test_recusa_calcular_sem_proponentes(self) -> None:
        with self.assertRaisesRegex(kpis.DadosInsuficientesKPI, "vencedores nao substituem"):
            kpis.calcular_participacao_me_local(
                self.licitacoes,
                pd.DataFrame(),
                coluna_licitacao="id",
                coluna_licitacao_participante="licitacao",
                coluna_porte="porte",
                coluna_municipio_empresa="municipio_empresa",
                coluna_municipio_comprador="municipio_comprador",
            )


if __name__ == "__main__":
    unittest.main()

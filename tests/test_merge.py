from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

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

import pandas as pd

from app.pipeline import cleaning, merge
from app.pipeline.ingestion import fornecedores, ibge, tce


def _fake_response(payload, status_code: int = 200) -> MagicMock:
    resposta = MagicMock()
    resposta.status_code = status_code
    resposta.json.return_value = payload
    resposta.raise_for_status.return_value = None
    return resposta


def _tabelas_pncp_com_itens_e_contrato() -> dict[str, pd.DataFrame]:
    """Contratacao PNCP real (unidadeOrgao + itens + contrato com niFornecedor)."""
    registro = {
        "numeroControlePNCP": "12345678000199-1-000005/2025",
        "anoCompra": 2025,
        "sequencialCompra": 5,
        "objetoCompra": "Aquisicao de material de expediente",
        "valorTotalEstimado": "500,00",
        "orgaoEntidade": {"cnpj": "12345678000199"},
        "unidadeOrgao": {"codigoIbge": 2301000, "ufSigla": "CE"},
        "itens": [
            {
                "numeroItem": 1,
                "descricao": "Caneta esferografica azul",
                "quantidade": "100",
                "valorUnitarioEstimado": "1,50",
                "valorTotal": "150,00",
                "unidadeMedida": "UN",
            },
            {
                "numeroItem": 2,
                "descricao": "Lapis grafite",
                "quantidade": "50",
                "valorUnitarioEstimado": "0,80",
                "valorTotal": "40,00",
                "unidadeMedida": "UN",
            },
        ],
        "contratos": [
            {
                # CNPJ do fornecedor vencedor formatado diferente do usado em
                # limpar_fornecedores — o join precisa normalizar os dois lados.
                "niFornecedor": "11.444.777/0001-61",
                "nomeRazaoSocialFornecedor": "Comércio Exemplo LTDA",
                "valorGlobal": "480,00",
                "tipoPessoa": "PJ",
            }
        ],
    }
    return cleaning.limpar_pncp_contratacoes([registro])


def _fornecedores_df_valido() -> pd.DataFrame:
    with patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj") as mock_opencnpj:
        mock_opencnpj.return_value = {
            "cnpj": "11444777000161",
            "razao_social": "Comércio Exemplo LTDA",
            "porte": "MICRO EMPRESA",
        }
        fornecedor = fornecedores.coletar_fornecedor("11.444.777/0001-61")

    return cleaning.limpar_fornecedores([fornecedor])


def _municipios_ibge_df() -> pd.DataFrame:
    abaiara = {
        "id": 2301000,
        "nome": "Abaiara",
        "microrregiao": {
            "id": 23014,
            "nome": "Baturite",
            "mesorregiao": {
                "id": 2303,
                "nome": "Norte Cearense",
                "UF": {"id": 23, "sigla": "CE", "nome": "Ceara"},
            },
        },
    }
    with patch("app.pipeline.ingestion.ibge.requests.get") as mock_get:
        mock_get.return_value = _fake_response([abaiara])
        registros = ibge.listar_municipios("CE")
    return cleaning.limpar_ibge_municipios(registros)


class JuntarItensPncpTest(unittest.TestCase):
    def test_junta_itens_por_numero_de_controle(self) -> None:
        tabelas = _tabelas_pncp_com_itens_e_contrato()

        resultado = merge.juntar_itens_pncp(tabelas)

        self.assertEqual(len(resultado), 2)  # 1 contratacao x 2 itens
        self.assertIn("objeto_compra", resultado.columns)
        self.assertIn("descricao", resultado.columns)
        self.assertTrue((resultado["numero_controle_pncp"] == "12345678000199-1-000005/2025").all())
        self.assertEqual(set(resultado["descricao"]), {"Caneta esferografica azul", "Lapis grafite"})

    def test_sem_tabela_de_itens_devolve_contratacoes_intactas(self) -> None:
        tabelas = {"contratacoes": pd.DataFrame([{"numero_controle_pncp": "A", "objeto_compra": "X"}])}

        resultado = merge.juntar_itens_pncp(tabelas)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado.iloc[0]["objeto_compra"], "X")


class EnriquecerComFornecedorTest(unittest.TestCase):
    def test_traz_dados_do_fornecedor_e_marca_nao_localizado(self) -> None:
        fornecedores_df = _fornecedores_df_valido()
        df = pd.DataFrame(
            [
                # formatado diferente do cnpj normalizado na base de fornecedores
                {"contrato_id": 1, "ni_fornecedor": "11.444.777/0001-61"},
                {"contrato_id": 2, "ni_fornecedor": "99.999.999/0001-00"},
            ]
        )

        resultado = merge.enriquecer_com_fornecedor(df, fornecedores_df, coluna_cnpj="ni_fornecedor")

        encontrado = resultado[resultado["contrato_id"] == 1].iloc[0]
        self.assertEqual(encontrado["fornecedor_porte_padronizado"], "ME")
        self.assertTrue(encontrado["fornecedor_elegivel_me"])

        nao_encontrado = resultado[resultado["contrato_id"] == 2].iloc[0]
        self.assertTrue(pd.isna(nao_encontrado["fornecedor_porte_padronizado"]))


class ExtrairCnpjsDistintosTest(unittest.TestCase):
    def test_deduplica_normaliza_e_descarta_documentos_invalidos(self) -> None:
        contratados = pd.Series(
            [
                "11.444.777/0001-61",
                "11444777000161",  # mesmo CNPJ do de cima, formatado diferente -> deduplica
                "111.444.777-35",  # CPF (11 digitos) -> descartado, base so cobre CNPJ
                "p3ADBbgv35B4W78xV4/xiA==",  # valor mascarado real visto no TCE -> descartado
                None,
            ]
        )
        contratos_pncp = pd.Series(["98.765.432/0001-11", None])

        resultado = merge.extrair_cnpjs_distintos(contratados, contratos_pncp)

        self.assertEqual(resultado, ["11444777000161", "98765432000111"])

    def test_ignora_series_none(self) -> None:
        contratados = pd.Series(["11.444.777/0001-61"])

        resultado = merge.extrair_cnpjs_distintos(contratados, None)

        self.assertEqual(resultado, ["11444777000161"])


class ValidarEEnriquecerMunicipioTest(unittest.TestCase):
    def test_traz_nome_uf_oficiais_e_sinaliza_divergencia(self) -> None:
        municipios = _municipios_ibge_df()
        df = pd.DataFrame(
            [
                {"id_registro": "A", "codigo_ibge": 2301000, "uf_informada": "CE"},
                {"id_registro": "B", "codigo_ibge": 2301000, "uf_informada": "SP"},
            ]
        )

        resultado = merge.validar_e_enriquecer_municipio(
            df, municipios, coluna_codigo_municipio="codigo_ibge", coluna_uf="uf_informada"
        )

        por_id = resultado.set_index("id_registro")
        self.assertEqual(por_id.loc["A", "municipio_nome"], "Abaiara")
        self.assertEqual(por_id.loc["A", "municipio_uf"], "CE")
        self.assertTrue(por_id.loc["A", "codigo_ibge_uf_confere"])
        self.assertFalse(por_id.loc["B", "codigo_ibge_uf_confere"])


class MontarBasePncpTest(unittest.TestCase):
    def test_integra_municipio_ibge_e_fornecedor(self) -> None:
        tabelas = _tabelas_pncp_com_itens_e_contrato()
        fornecedores_df = _fornecedores_df_valido()
        municipios = _municipios_ibge_df()

        resultado = merge.montar_base_pncp(
            tabelas, fornecedores_df=fornecedores_df, municipios_ibge=municipios
        )

        contratacoes = resultado["contratacoes"]
        self.assertEqual(contratacoes.iloc[0]["municipio_nome"], "Abaiara")
        self.assertTrue(contratacoes.iloc[0]["unidade_orgao_codigo_ibge_uf_confere"])

        contratos = resultado["contratos"]
        self.assertEqual(contratos.iloc[0]["fornecedor_porte_padronizado"], "ME")


def _df_tce_contratos() -> pd.DataFrame:
    """
    'contratos' isolado — nomes de campo confirmados ao vivo em
    api-dados-abertos.tce.ce.gov.br/sim/contratos. Note que NAO tem CNPJ do
    contratado: isso so existe no endpoint separado 'contratados'.
    """
    registros_brutos = [
        {
            "codigo_municipio": "010",  # codigo INTERNO do TCE-CE, nao e o codigo IBGE
            "numero_contrato": "2025000123",
            "data_contrato": "2025-01-15",
            "modalide_contrato": "Pregao Eletronico",
            "data_inicio_vigencia_contrato": "2025-01-15",
            "data_fim_vigencia_contrato": "2026-01-15",
            "descricao_objeto_contrato": "Prestacao de servicos de limpeza predial",
            "valor_total_contrato": "50.000,00",
            "cpf_gestor": "111.444.777-35",
        }
    ]
    with patch("app.pipeline.ingestion.tce.requests.get") as mock_get:
        mock_get.return_value = _fake_response({"elements": registros_brutos})
        registros = tce.buscar_contratos("20250101", "20250301", codigo_municipio="010")
    return cleaning.limpar_tce(registros)


def _df_tce_contratados() -> pd.DataFrame:
    """'contratados' isolado — e onde fica o CNPJ/CPF do contratado (`numero_documento_negociante`)."""
    registros_brutos = [
        {
            "codigo_municipio": "010",
            "numero_contrato": "2025000123",  # liga com 'contratos' por (numero_contrato, codigo_municipio)
            "numero_documento_negociante": "11.444.777/0001-61",
            "codigo_tipo_negociante": "PJ",
            "nome_negociante": "Comércio Exemplo LTDA",
            "cep_negociante": "62240000",
        }
    ]
    with patch("app.pipeline.ingestion.tce.requests.get") as mock_get:
        mock_get.return_value = _fake_response({"elements": registros_brutos})
        registros = tce.buscar_contratados("20250101", "20250301", codigo_municipio="010")
    return cleaning.limpar_tce(registros)


class MontarBaseTceTest(unittest.TestCase):
    def test_junta_contratados_e_enriquece_fornecedor_mas_nao_cruza_municipio_ibge(self) -> None:
        df_contratos = _df_tce_contratos()
        df_contratados = _df_tce_contratados()
        fornecedores_df = _fornecedores_df_valido()

        resultado = merge.montar_base_tce(df_contratos, df_contratados, fornecedores_df=fornecedores_df)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado.iloc[0]["nome_negociante"], "Comércio Exemplo LTDA")  # veio de 'contratados'
        self.assertEqual(resultado.iloc[0]["fornecedor_porte_padronizado"], "ME")
        self.assertNotIn("municipio_nome", resultado.columns)  # sem crosswalk TCE<->IBGE, nao inventa join

    def test_sem_contratados_nao_enriquece_fornecedor(self) -> None:
        # 'contratos' sozinho nao tem CNPJ do contratado — nao ha como enriquecer.
        df_contratos = _df_tce_contratos()

        resultado = merge.montar_base_tce(df_contratos, fornecedores_df=_fornecedores_df_valido())

        self.assertEqual(len(resultado), 1)
        self.assertNotIn("fornecedor_porte_padronizado", resultado.columns)


class JuntarContratosEContratadosTest(unittest.TestCase):
    def test_nao_junta_por_chave_parcial_quando_falta_codigo_municipio(self) -> None:
        # Achado de revisao de codigo: com 'codigo_municipio' ausente em
        # 'contratados', o join usava so 'numero_contrato' e colava o
        # contratado errado num contrato de outro municipio.
        df_contratos = pd.DataFrame(
            [
                {"numero_contrato": "100", "codigo_municipio": "010", "valor_total_contrato": 5000.0},
                {"numero_contrato": "100", "codigo_municipio": "020", "valor_total_contrato": 9000.0},
            ]
        )
        df_contratados = pd.DataFrame(
            [
                {"numero_contrato": "100", "nome_negociante": "Empresa do municipio 010"},
            ]
        )

        resultado = merge.juntar_contratos_e_contratados(df_contratos, df_contratados)

        self.assertNotIn("nome_negociante", resultado.columns)
        self.assertEqual(len(resultado), 2)


class UnirPncpETceTest(unittest.TestCase):
    def test_uniao_preserva_todas_as_colunas_dos_dois_lados(self) -> None:
        df_pncp = _tabelas_pncp_com_itens_e_contrato()["contratacoes"]
        df_tce = _df_tce_contratos()

        resultado = merge.unir_pncp_e_tce(df_pncp, df_tce)

        self.assertEqual(len(resultado), 2)
        self.assertEqual(set(resultado["fonte"]), {"PNCP", "TCE"})

        # nenhuma coluna de nenhum dos dois lados foi descartada
        for coluna in df_pncp.columns:
            self.assertIn(coluna, resultado.columns)
        for coluna in df_tce.columns:
            self.assertIn(coluna, resultado.columns)

        linha_pncp = resultado[resultado["fonte"] == "PNCP"].iloc[0]
        linha_tce = resultado[resultado["fonte"] == "TCE"].iloc[0]

        # colunas exclusivas do PNCP ficam vazias na linha do TCE, e vice-versa
        self.assertEqual(linha_pncp["numero_controle_pncp"], "12345678000199-1-000005/2025")
        self.assertTrue(pd.isna(linha_tce["numero_controle_pncp"]))
        self.assertEqual(linha_tce["numero_contrato"], "2025000123")
        self.assertTrue(pd.isna(linha_pncp["numero_contrato"]))

    def test_um_lado_vazio_devolve_so_o_outro_com_coluna_fonte(self) -> None:
        df_pncp = _tabelas_pncp_com_itens_e_contrato()["contratacoes"]

        resultado = merge.unir_pncp_e_tce(df_pncp, pd.DataFrame())

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado.iloc[0]["fonte"], "PNCP")

    def test_ambos_vazios_devolve_dataframe_vazio(self) -> None:
        resultado = merge.unir_pncp_e_tce(pd.DataFrame(), None)
        self.assertTrue(resultado.empty)


if __name__ == "__main__":
    unittest.main()

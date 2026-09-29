from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

import pandas as pd

from app.pipeline import merge
from app.pipeline.cleaners import ibge as ibge_cleaning
from app.pipeline.cleaners import opencnpj as opencnpj_cleaning
from app.pipeline.cleaners import tce as tce_cleaning
from app.pipeline.ingestion import fornecedores, ibge, tce


def _fake_response(payload, status_code: int = 200) -> MagicMock:
    resposta = MagicMock()
    resposta.status_code = status_code
    resposta.json.return_value = payload
    resposta.raise_for_status.return_value = None
    return resposta


def _fornecedores_df_valido() -> pd.DataFrame:
    with patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj") as mock_opencnpj:
        mock_opencnpj.return_value = {
            "cnpj": "11444777000161",
            "razao_social": "Comércio Exemplo LTDA",
            "porte_empresa": "MICRO EMPRESA",
            "municipio": "AMONTADA",
            "uf": "CE",
            "cnae_principal": "6201501",
            "cnaes_secundarios": ["4751201", "9511800"],
        }
        fornecedor = fornecedores.coletar_fornecedor("11.444.777/0001-61")

    return opencnpj_cleaning.limpar_fornecedores([fornecedor])


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
    return ibge_cleaning.limpar_municipios(registros)


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

    def test_enriquece_despesa_com_porte_municipio_sede_e_cnae_para_cnpj_conhecido(self) -> None:
        fornecedores_df = _fornecedores_df_valido()
        despesas = pd.DataFrame(
            [
                {
                    "despesa_id": "D1",
                    "cnpj_fornecedor": "11.444.777/0001-61",
                    "valor": 1500.0,
                }
            ]
        )

        resultado = merge.enriquecer_com_fornecedor(
            despesas,
            fornecedores_df,
            coluna_cnpj="cnpj_fornecedor",
        )

        despesa = resultado.iloc[0]
        self.assertEqual(despesa["fornecedor_porte_padronizado"], "ME")
        self.assertEqual(despesa["fornecedor_municipio_sede"], "AMONTADA")
        self.assertEqual(despesa["fornecedor_uf_sede"], "CE")
        self.assertEqual(despesa["fornecedor_cnae_principal_codigo"], "6201501")
        self.assertTrue(pd.isna(despesa["fornecedor_cnae_principal_descricao"]))
        self.assertEqual(despesa["fornecedor_cnaes"], ["4751201", "9511800"])


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
        fornecedores_adicionais = pd.Series(["98.765.432/0001-11", None])

        resultado = merge.extrair_cnpjs_distintos(contratados, fornecedores_adicionais)

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
        registros = tce.buscar_contratos("2025-01-01", "2025-03-01", codigo_municipio="010")
    return tce_cleaning.limpar(registros)


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
        registros = tce.buscar_contratados("2025-01-01", "2025-03-01", codigo_municipio="010")
    return tce_cleaning.limpar(registros)


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


def _fake_response(payload, status_code: int = 200) -> MagicMock:
    resposta = MagicMock()
    resposta.status_code = status_code
    resposta.json.return_value = payload
    resposta.raise_for_status.return_value = None
    return resposta


UFS_BRASIL = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO",
    "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
    "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)


class ClassificarOrigemGeograficaTest(unittest.TestCase):
    def test_caso_1_mesmo_municipio(self) -> None:
        resultado = merge.classificar_origem_geografica("Fortaleza", "CE", "Fortaleza")
        self.assertEqual(resultado, merge.NIVEL_MESMO_MUNICIPIO)

    def test_caso_2_outro_municipio_ce(self) -> None:
        resultado = merge.classificar_origem_geografica("Fortaleza", "CE", "Sobral")
        self.assertEqual(resultado, merge.NIVEL_OUTRO_MUNICIPIO_CE)

    def test_caso_3_fora_do_estado(self) -> None:
        # Cobre TODOS os estados do Brasil, exceto o CE (26 UFs) — garante
        # que a classificacao escala para qualquer estado, nao so os
        # exemplos mais comuns (SP, RJ etc.).
        for uf in UFS_BRASIL:
            if uf == "CE":
                continue
            with self.subTest(uf=uf):
                resultado = merge.classificar_origem_geografica("Fortaleza", uf, "Qualquer Município")
                self.assertEqual(resultado, merge.NIVEL_FORA_DO_ESTADO)


class BuscarMunicipiosUfTest(unittest.TestCase):
    def test_retorna_nomes_ordenados_e_sem_duplicatas(self) -> None:
        payload = [
            {"nome": "Sobral"},
            {"nome": "Fortaleza"},
            {"nome": "Juazeiro do Norte"},
            {"nome": "Sobral"},  # duplicata proposital
        ]
        with patch("app.pipeline.enrichment.municipios.requests.get") as mock_get:
            mock_get.return_value = _fake_response(payload)
            resultado = merge.buscar_municipios_uf("CE")

        self.assertEqual(resultado, ["Fortaleza", "Juazeiro do Norte", "Sobral"])
        mock_get.assert_called_once_with(
            "https://servicodados.ibge.gov.br/api/v1/localidades/estados/CE/municipios",
            timeout=10,
        )

    def test_uf_minuscula_e_normalizada_na_url(self) -> None:
        with patch("app.pipeline.enrichment.municipios.requests.get") as mock_get:
            mock_get.return_value = _fake_response([{"nome": "São Paulo"}])
            merge.buscar_municipios_uf("sp")

        mock_get.assert_called_once_with(
            "https://servicodados.ibge.gov.br/api/v1/localidades/estados/SP/municipios",
            timeout=10,
        )

if __name__ == "__main__":
    unittest.main()

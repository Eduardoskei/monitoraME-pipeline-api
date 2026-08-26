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

from app.pipeline import cleaning
from app.pipeline.ingestion import fornecedores, ibge, pncp, tce


def _fake_response(payload, status_code: int = 200) -> MagicMock:
    resposta = MagicMock()
    resposta.status_code = status_code
    resposta.json.return_value = payload
    resposta.raise_for_status.return_value = None
    return resposta


class LimparPncpContratacoesTest(unittest.TestCase):
    """Usa app.pipeline.ingestion.pncp de verdade (só a chamada HTTP é mockada)."""

    @patch("app.pipeline.ingestion.pncp.requests.get")
    def test_pipeline_completo_a_partir_da_ingestao_real(self, mock_get) -> None:
        registros_brutos = [
            {
                "numeroControlePNCP": "12345678000199-1-000001/2025",
                "anoCompra": 2025,
                "sequencialCompra": 1,
                "objetoCompra": "  Aquisicao de material de escritorio  ",
                "valorTotalEstimado": "15000,50",
                "valorTotalHomologado": "14800,00",
                "dataAberturaProposta": "2025-01-10T09:00:00",
                "dataEncerramentoProposta": "2025-01-20T18:00:00",
                "dataPublicacaoPncp": "2025-01-05",
                "dataInclusao": "2025-01-05",
                "dataAtualizacao": "2025-01-06",
                "modalidadeNome": "Pregao Eletronico",
                "situacaoCompraNome": "Divulgada no PNCP",
                "orgaoEntidade": {
                    # CNPJ com 14 digitos mas DV invalido (erro comum de digitacao) —
                    # o pipeline deve manter a linha e so sinalizar em '_valido'.
                    "cnpj": "12345678000199",
                    "razaoSocial": "Prefeitura Municipal de São Gonçalo do Amarante",
                },
                "campoTotalmenteVazio": None,
            },
            # duplicata exata do primeiro registro (ex.: reconsulta da mesma pagina)
            {
                "numeroControlePNCP": "12345678000199-1-000001/2025",
                "anoCompra": 2025,
                "sequencialCompra": 1,
                "objetoCompra": "  Aquisicao de material de escritorio  ",
                "valorTotalEstimado": "15000,50",
                "valorTotalHomologado": "14800,00",
                "dataAberturaProposta": "2025-01-10T09:00:00",
                "dataEncerramentoProposta": "2025-01-20T18:00:00",
                "dataPublicacaoPncp": "2025-01-05",
                "dataInclusao": "2025-01-05",
                "dataAtualizacao": "2025-01-06",
                "modalidadeNome": "Pregao Eletronico",
                "situacaoCompraNome": "Divulgada no PNCP",
                "orgaoEntidade": {
                    "cnpj": "12345678000199",
                    "razaoSocial": "Prefeitura Municipal de Amontada",
                },
                "campoTotalmenteVazio": None,
            },
            {
                "numeroControlePNCP": "12345678000199-1-000002/2025",
                "anoCompra": 2025,
                "sequencialCompra": 2,
                "objetoCompra": "Contratacao de servicos de limpeza",
                "valorTotalEstimado": "8000,00",
                "valorTotalHomologado": None,
                "dataAberturaProposta": "2025-02-01T09:00:00",
                "dataEncerramentoProposta": "2025-02-10T18:00:00",
                "dataPublicacaoPncp": "2025-01-25",
                # com offset de fuso (formato comum da API do PNCP) -> deve normalizar p/ UTC
                "dataInclusao": "2025-01-25T08:00:00-03:00",
                "dataAtualizacao": "2025-01-25",
                "modalidadeNome": "Pregao Eletronico",
                "situacaoCompraNome": "Divulgada no PNCP",
                "orgaoEntidade": {
                    "cnpj": "12345678000199",
                    "razaoSocial": "Prefeitura Municipal de Amontada",
                },
                "campoTotalmenteVazio": None,
            },
            # sem numero de controle -> registro nao identificavel, deve ser descartado
            {
                "numeroControlePNCP": "",
                "anoCompra": 2025,
                "sequencialCompra": 3,
                "objetoCompra": "Registro invalido sem numero de controle",
                "valorTotalEstimado": "1000,00",
                "campoTotalmenteVazio": None,
            },
        ]
        mock_get.return_value = _fake_response({"data": registros_brutos})

        registros = pncp.buscar_contratacoes_publicadas("2025-01-01", "2025-03-01")
        self.assertEqual(len(registros), 4)  # ingestao real trouxe os 4 registros brutos

        tabelas = cleaning.limpar_pncp_contratacoes(registros)
        df = tabelas["contratacoes"]

        self.assertEqual(len(df), 2)  # duplicata e registro sem chave removidos
        self.assertNotIn("campo_totalmente_vazio", df.columns)  # coluna 100% vazia removida
        self.assertIn("orgao_entidade_razao_social", df.columns)  # dict aninhado achatado

        primeiro = df[df["numero_controle_pncp"] == "12345678000199-1-000001/2025"].iloc[0]
        self.assertEqual(primeiro["objeto_compra"], "Aquisicao de material de escritorio")
        self.assertEqual(primeiro["valor_total_estimado"], 15000.5)
        self.assertEqual(primeiro["data_abertura_proposta"], "2025-01-10T09:00:00")
        self.assertEqual(primeiro["data_publicacao_pncp"], "2025-01-05")
        self.assertEqual(primeiro["orgao_entidade_cnpj"], "12345678000199")
        self.assertFalse(primeiro["orgao_entidade_cnpj_valido"])  # DV invalido, mas linha nao foi descartada
        # nome do orgao ganha uma chave normalizada (sem acento/maiusculo) p/ agregacao
        self.assertEqual(
            primeiro["orgao_entidade_razao_social_chave"],
            "PREFEITURA MUNICIPAL DE SAO GONCALO DO AMARANTE",
        )

        segundo = df[df["numero_controle_pncp"] == "12345678000199-1-000002/2025"].iloc[0]
        self.assertTrue(pd.isna(segundo["valor_total_homologado"]))  # nulo numerico preservado (nao virou 0)
        # offset -03:00 normalizado para UTC em vez de descartado ao serializar
        self.assertEqual(segundo["data_inclusao"], "2025-01-25T11:00:00Z")

    def test_lista_de_itens_vira_tabela_filha_ligada_ao_registro_pai(self) -> None:
        # Formato plausivel de uma contratacao com os itens ja embutidos
        # (mesmos nomes de campo usados em pncp.consultar_itens_compra).
        registro = {
            "numeroControlePNCP": "12345678000199-1-000005/2025",
            "anoCompra": 2025,
            "sequencialCompra": 5,
            "objetoCompra": "Aquisicao de material de expediente",
            "valorTotalEstimado": "500,00",
            "orgaoEntidade": {"cnpj": "12345678000199"},
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
        }

        tabelas = cleaning.limpar_pncp_contratacoes([registro])

        self.assertNotIn("itens", tabelas["contratacoes"].columns)
        self.assertIn("itens", tabelas)
        itens = tabelas["itens"]
        self.assertEqual(len(itens), 2)
        self.assertTrue((itens["numero_controle_pncp"] == "12345678000199-1-000005/2025").all())
        self.assertEqual(sorted(itens["valor_total"].tolist()), [40.0, 150.0])

    def test_ni_fornecedor_com_zero_a_esquerda_nao_vira_numero(self) -> None:
        # Achado de revisao de codigo: 'ni_fornecedor' (CNPJ/CPF do fornecedor
        # vencedor) nao batia em _PADRAO_COLUNA_IDENTIFICADOR e virava Int64,
        # destruindo o zero a esquerda e quebrando o enriquecimento por CNPJ.
        registro = {
            "numeroControlePNCP": "12345678000199-1-000009/2025",
            "orgaoEntidade": {"cnpj": "12345678000199"},
            "contratos": [
                {
                    "niFornecedor": "01234567000199",
                    "nomeRazaoSocialFornecedor": "Fornecedor Exemplo",
                    "valorGlobal": "100,00",
                }
            ],
        }

        tabelas = cleaning.limpar_pncp_contratacoes([registro])

        self.assertEqual(tabelas["contratos"]["ni_fornecedor"].iloc[0], "01234567000199")


class LimparTceTest(unittest.TestCase):
    """
    Usa app.pipeline.ingestion.tce de verdade (só a chamada HTTP é mockada).

    Os nomes de campo abaixo foram confirmados consultando ao vivo
    api-dados-abertos.tce.ce.gov.br/sim/contratos — inclusive o typo real
    da API ('modalide_contrato', sem o 'da') e o fato de 'numero_contrato'
    vir como string puramente numerica (ex.: "2025000123").
    """

    @patch("app.pipeline.ingestion.tce.requests.get")
    def test_pipeline_completo_a_partir_da_ingestao_real(self, mock_get) -> None:
        registros_brutos = [
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "data_contrato": "2025-01-15",
                "tipo_contrato": "1",
                "modalide_contrato": "Pregao Eletronico",
                "data_inicio_vigencia_contrato": "2025-01-15",
                "data_fim_vigencia_contrato": "2026-01-15",
                "descricao_objeto_contrato": "  Prestacao de servicos de limpeza predial  ",
                "valor_total_contrato": "50.000,00",
                "cpf_gestor": "111.444.777-35",
                "nome_fiscal_contrato": "Fulano Fiscal",
                "cpf_fiscal_contrato": "111.444.777-35",
                "data_referencia_doc": "2025-01-15",
                "numero_processo_adm": "2025000045",
                # confirmado ao vivo: quase sempre vazio (0% em 227 registros de amostra)
                "numero_id_contrato_pncp": None,
            },
            # duplicata exata do primeiro contrato
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000123",
                "data_contrato": "2025-01-15",
                "tipo_contrato": "1",
                "modalide_contrato": "Pregao Eletronico",
                "data_inicio_vigencia_contrato": "2025-01-15",
                "data_fim_vigencia_contrato": "2026-01-15",
                "descricao_objeto_contrato": "  Prestacao de servicos de limpeza predial  ",
                "valor_total_contrato": "50.000,00",
                "cpf_gestor": "111.444.777-35",
                "nome_fiscal_contrato": "Fulano Fiscal",
                "cpf_fiscal_contrato": "111.444.777-35",
                "data_referencia_doc": "2025-01-15",
                "numero_processo_adm": "2025000045",
                "numero_id_contrato_pncp": None,
            },
            {
                "codigo_municipio": "010",
                "numero_contrato": "2025000456",
                "data_contrato": "2025-02-01",
                "tipo_contrato": "1",
                "modalide_contrato": "Pregao Eletronico",
                "data_inicio_vigencia_contrato": "2025-02-01",
                "data_fim_vigencia_contrato": "2025-12-31",
                "descricao_objeto_contrato": "Fornecimento de material de expediente",
                "valor_total_contrato": "12.500,75",
                "cpf_gestor": "111.444.777-35",
                "nome_fiscal_contrato": "Fulano Fiscal",
                "cpf_fiscal_contrato": "111.444.777-35",
                "data_referencia_doc": "2025-02-01",
                "numero_processo_adm": "2025000046",
                # caso raro (confirmado ao vivo: ~10% em 2024-25, praticamente 0% antes) em
                # que o TCE preencheu a referencia ao PNCP
                "numero_id_contrato_pncp": "12345678000199-1-000005/2025",
            },
        ]
        mock_get.return_value = _fake_response({"elements": registros_brutos})

        registros = tce.buscar_contratos("20250101", "20250301", codigo_municipio="010")
        self.assertEqual(len(registros), 3)  # ingestao real trouxe os 3 registros brutos

        df = cleaning.limpar_tce(registros, chave_duplicata=["numero_contrato"])

        self.assertEqual(len(df), 2)  # duplicata removida
        # "codigo_municipio" e um identificador e nao pode virar numero, senao
        # perde o zero a esquerda ("010" -> 10).
        self.assertTrue((df["codigo_municipio"] == "010").all())
        # "numero_contrato" e puramente numerico na API real ("2025000123") — sem a
        # protecao de identificador, isso viraria Int64 e perderia o sentido de codigo.
        self.assertEqual(df["numero_contrato"].tolist(), ["2025000123", "2025000456"])
        self.assertEqual(sorted(df["valor_total_contrato"].tolist()), [12500.75, 50000.0])

        primeiro = df[df["numero_contrato"] == "2025000123"].iloc[0]
        self.assertEqual(primeiro["descricao_objeto_contrato"], "Prestacao de servicos de limpeza predial")
        self.assertEqual(primeiro["data_contrato"], "2025-01-15")
        # cpf_gestor casa com o padrao de CPF automaticamente (bate 'cpf' no nome da
        # coluna) — normalizado para digitos e validado, sem eu precisar de codigo extra.
        self.assertEqual(primeiro["cpf_gestor"], "11144477735")
        self.assertTrue(primeiro["cpf_gestor_valido"])
        # confirmado ao vivo: quase sempre vazio — aqui esta ausente pra este contrato;
        # e coluna de texto, entao tratar_nulos preenche com o marcador padrao (nao dropa a linha)
        self.assertEqual(primeiro["numero_id_contrato_pncp"], "nao_informado")

        segundo = df[df["numero_contrato"] == "2025000456"].iloc[0]
        self.assertEqual(segundo["numero_id_contrato_pncp"], "12345678000199-1-000005/2025")


class LimparIbgeMunicipiosTest(unittest.TestCase):
    """Usa app.pipeline.ingestion.ibge de verdade (só a chamada HTTP é mockada)."""

    @patch("app.pipeline.ingestion.ibge.requests.get")
    def test_pipeline_completo_a_partir_da_ingestao_real(self, mock_get) -> None:
        abaiara = {
            "id": 2301000,
            "nome": "Abaiara",
            "observacao": None,
            "microrregiao": {
                "id": 23014,
                "nome": "Baturite",
                "mesorregiao": {
                    "id": 2303,
                    "nome": "Norte Cearense",
                    "UF": {
                        "id": 23,
                        "sigla": "CE",
                        "nome": "Ceara",
                        "regiao": {"id": 2, "sigla": "NE", "nome": "Nordeste"},
                    },
                },
            },
            "regiao-imediata": {
                "id": 230006,
                "nome": "Baturite",
                "regiao-intermediaria": {
                    "id": 2303,
                    "nome": "Fortaleza",
                    "UF": {
                        "id": 23,
                        "sigla": "CE",
                        "nome": "Ceara",
                        "regiao": {"id": 2, "sigla": "NE", "nome": "Nordeste"},
                    },
                },
            },
        }
        acarape = {
            "id": 2301109,
            "nome": "Acarape",
            "observacao": None,
            "microrregiao": {
                "id": 23011,
                "nome": "Baturite",
                "mesorregiao": {
                    "id": 2303,
                    "nome": "Norte Cearense",
                    "UF": {
                        "id": 23,
                        "sigla": "CE",
                        "nome": "Ceara",
                        "regiao": {"id": 2, "sigla": "NE", "nome": "Nordeste"},
                    },
                },
            },
            "regiao-imediata": {
                "id": 230006,
                "nome": "Baturite",
                "regiao-intermediaria": {
                    "id": 2303,
                    "nome": "Fortaleza",
                    "UF": {
                        "id": 23,
                        "sigla": "CE",
                        "nome": "Ceara",
                        "regiao": {"id": 2, "sigla": "NE", "nome": "Nordeste"},
                    },
                },
            },
        }
        sem_id = {"id": None, "nome": "Municipio invalido", "observacao": None}

        mock_get.return_value = _fake_response([abaiara, acarape, sem_id, dict(abaiara)])

        registros = ibge.listar_municipios("CE")
        self.assertEqual(len(registros), 4)  # ingestao real trouxe os 4 registros brutos

        df = cleaning.limpar_ibge_municipios(registros)

        self.assertEqual(len(df), 2)  # sem id e duplicata removidos
        self.assertEqual(str(df["id"].dtype), "Int64")
        self.assertNotIn("observacao", df.columns)  # coluna 100% vazia removida
        self.assertIn("microrregiao_mesorregiao_uf_sigla", df.columns)
        self.assertIn("regiao_imediata_regiao_intermediaria_uf_regiao_nome", df.columns)

        linha = df[df["id"] == 2301000].iloc[0]
        self.assertEqual(linha["microrregiao_mesorregiao_uf_sigla"], "CE")
        self.assertEqual(linha["microrregiao_mesorregiao_uf_regiao_nome"], "Nordeste")
        self.assertEqual(linha["regiao_imediata_regiao_intermediaria_uf_regiao_nome"], "Nordeste")


class LimparFornecedoresTest(unittest.TestCase):
    """Usa app.pipeline.ingestion.fornecedores de verdade (só OpenCNPJ é mockada)."""

    @patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj")
    def test_pipeline_completo_a_partir_da_ingestao_real(self, mock_opencnpj) -> None:
        mock_opencnpj.return_value = {
            # CNPJ matematicamente valido (digitos verificadores corretos)
            "cnpj": "11444777000161",
            "razao_social": "Comércio Exemplo LTDA",
            "nome_fantasia": "Exemplo Comercio",
            "descricao_situacao_cadastral": "ATIVA",
            "porte": "MICRO EMPRESA",
            "capital_social": 100000,
            "municipio": "AMONTADA",
            "uf": "CE",
            "cep": "62240000",
            "ddd_telefone_1": "8834321234",
            "opcao_pelo_simples": True,
            "data_opcao_pelo_simples": "2018-01-01",
            "data_exclusao_do_simples": None,
            "opcao_pelo_mei": False,
            "data_opcao_pelo_mei": None,
            "qsa": [
                {"nome_socio": "Fulano de Tal", "qualificacao_socio": "Socio-Administrador"},
            ],
            "socios": [
                {"nome": "Fulano de Tal", "qualificacao": "Socio-Administrador"},
            ],
        }

        # duas coletas do mesmo CNPJ (ex.: fornecedor presente em 2 contratacoes distintas)
        fornecedor_1 = fornecedores.coletar_fornecedor("11.444.777/0001-61")
        fornecedor_2 = fornecedores.coletar_fornecedor("11.444.777/0001-61")

        df = cleaning.limpar_fornecedores([fornecedor_1, fornecedor_2])

        self.assertEqual(len(df), 1)  # duplicata pelo cnpj removida
        self.assertEqual(df.iloc[0]["cnpj"], "11444777000161")
        self.assertTrue(df.iloc[0]["cnpj_valido"])  # DV correto
        self.assertEqual(df.iloc[0]["razao_social"], "Comércio Exemplo LTDA")
        # chave normalizada (sem acento/maiusculo) para agregacao/join, sem alterar o texto original
        self.assertEqual(df.iloc[0]["razao_social_chave"], "COMERCIO EXEMPLO LTDA")
        # colunas identificadoras (cep/telefone) nao podem virar numero
        self.assertEqual(df.iloc[0]["opencnpj_cep"], "62240000")
        self.assertEqual(df.iloc[0]["opencnpj_ddd_telefone_1"], "8834321234")
        self.assertEqual(df.iloc[0]["opencnpj_descricao_situacao_cadastral"], "ATIVA")
        # porte padronizado para o vocabulario fixo usado na analise de compras ME
        self.assertEqual(df.iloc[0]["porte_padronizado"], "ME")
        self.assertTrue(df.iloc[0]["elegivel_me"])
        # optante pelo Simples Nacional (regime tributario) — criterio distinto do porte
        self.assertTrue(df.iloc[0]["optante_simples_nacional"])
        self.assertEqual(df.iloc[0]["data_opcao_simples_nacional"], "2018-01-01")
        self.assertFalse(df.iloc[0]["optante_mei"])
        # lista de dicts (socios) e mantida intacta, sem quebrar a deduplicacao por cnpj
        self.assertEqual(
            df.iloc[0]["opencnpj_qsa"],
            [{"nome_socio": "Fulano de Tal", "qualificacao_socio": "Socio-Administrador"}],
        )

    def test_porte_da_opencnpj_pode_vir_aninhado(self) -> None:
        df = cleaning.limpar_fornecedores([
            {
                "cnpj": "11444777000161",
                "opencnpj": {"porte": {"descricao": "MICRO EMPRESA"}},
            }
        ])

        self.assertEqual(df.iloc[0]["porte_padronizado"], "ME")
        self.assertTrue(df.iloc[0]["elegivel_me"])

    def test_epp_nao_e_elegivel_no_kpi_de_me(self) -> None:
        df = cleaning.limpar_fornecedores([
            {
                "cnpj": "98765432000111",
                "opencnpj": {"porte": "EMPRESA DE PEQUENO PORTE"},
            }
        ])

        self.assertEqual(df.iloc[0]["porte_padronizado"], "EPP")
        self.assertFalse(df.iloc[0]["elegivel_me"])

    def test_opcao_simples_e_mei_aceitam_ausente_e_texto(self) -> None:
        df = cleaning.limpar_fornecedores(
            [
                {
                    "cnpj": "11444777000161",
                    "opencnpj": {
                        "porte": "MICRO EMPRESA",
                        "opcao_pelo_simples": None,
                        "opcao_pelo_mei": "não",
                    },
                    "porte": "MICRO EMPRESA",
                },
                {
                    "cnpj": "98765432000111",
                    "opencnpj": {
                        "porte": "DEMAIS",
                        "opcao_pelo_simples": "sim",
                        "opcao_pelo_mei": None,
                    },
                    "porte": "DEMAIS",
                },
            ]
        )

        por_cnpj = df.set_index("cnpj")
        self.assertTrue(pd.isna(por_cnpj.loc["11444777000161", "optante_simples_nacional"]))
        self.assertFalse(por_cnpj.loc["11444777000161", "optante_mei"])
        self.assertTrue(por_cnpj.loc["98765432000111", "optante_simples_nacional"])
        self.assertTrue(pd.isna(por_cnpj.loc["98765432000111", "optante_mei"]))

    def test_sem_porte_mantem_elegibilidade_nula_para_kpi(self) -> None:
        df = cleaning.limpar_fornecedores(
            [
                {
                    "cnpj": "11444777000161",
                    "opencnpj": {},
                }
            ]
        )

        self.assertIn("porte_padronizado", df.columns)
        self.assertIn("elegivel_me", df.columns)
        self.assertTrue(pd.isna(df.iloc[0]["porte_padronizado"]))
        self.assertTrue(pd.isna(df.iloc[0]["elegivel_me"]))


class PadronizarNomesColunasTest(unittest.TestCase):
    def test_sufixo_gerado_nao_colide_com_coluna_original(self) -> None:
        # Achado de revisao de codigo: ['A', 'A', 'A_1'] virava ['a', 'a_1', 'a_1']
        # (o sufixo gerado para a 2a 'A' colidia com a coluna original 'A_1').
        df = pd.DataFrame([[1, 2, 3]], columns=["A", "A", "A_1"])

        resultado = cleaning.padronizar_nomes_colunas(df)

        self.assertEqual(len(set(resultado.columns)), 3)
        self.assertEqual(list(resultado.columns), ["a", "a_1", "a_1_1"])


class ConverterDatasTimestampTest(unittest.TestCase):
    def test_timestamp_naive_a_meia_noite_vira_data_sem_horario(self) -> None:
        # Achado de revisao de codigo: um pd.Timestamp naive `.isoformat()` sempre
        # incluia o horario, mesmo a meia-noite — divergindo do mesmo valor
        # chegando como string (que vira 'YYYY-MM-DD' quando nao tem horario).
        df = pd.DataFrame(
            {
                "data_x": [
                    pd.Timestamp("2025-01-15 00:00:00"),
                    pd.Timestamp("2025-01-15 09:30:00"),
                ]
            }
        )

        resultado = cleaning.converter_datas(df, ["data_x"])

        self.assertEqual(resultado["data_x"].tolist(), ["2025-01-15", "2025-01-15T09:30:00"])


class DocumentoValidationTest(unittest.TestCase):
    """Testes de unidade dos validadores de CNPJ/CPF (usados por padronizar_documentos)."""

    def test_validar_cnpj_aceita_digito_verificador_correto(self) -> None:
        self.assertTrue(cleaning.validar_cnpj("11444777000161"))

    def test_validar_cnpj_rejeita_digito_verificador_incorreto(self) -> None:
        self.assertFalse(cleaning.validar_cnpj("12345678000199"))

    def test_validar_cnpj_rejeita_sequencia_repetida(self) -> None:
        self.assertFalse(cleaning.validar_cnpj("11111111111111"))

    def test_validar_cpf_aceita_digito_verificador_correto(self) -> None:
        self.assertTrue(cleaning.validar_cpf("11144477735"))

    def test_validar_cpf_rejeita_digito_verificador_incorreto(self) -> None:
        self.assertFalse(cleaning.validar_cpf("11144477736"))

    def test_normalizar_cnpj_rejeita_quantidade_errada_de_digitos(self) -> None:
        self.assertIsNone(cleaning.normalizar_cnpj("123"))
        self.assertEqual(cleaning.normalizar_cnpj("11.444.777/0001-61"), "11444777000161")

    def test_padronizar_documentos_detecta_colunas_cnpj_e_cpf_por_nome(self) -> None:
        df = pd.DataFrame(
            [
                {"cnpj_orgao": "11.444.777/0001-61", "cpf_responsavel": "111.444.777-35"},
                {"cnpj_orgao": "abc", "cpf_responsavel": None},  # cnpj sem 14 digitos -> vira nulo
            ]
        )

        resultado = cleaning.padronizar_documentos(df)

        self.assertEqual(resultado["cnpj_orgao"].tolist(), ["11444777000161", None])
        self.assertEqual(resultado["cnpj_orgao_valido"].tolist(), [True, False])
        self.assertEqual(resultado["cpf_responsavel"].tolist(), ["11144477735", None])
        self.assertEqual(resultado["cpf_responsavel_valido"].tolist(), [True, False])

    def test_converter_numericos_nao_transforma_booleano_em_1_0(self) -> None:
        # bool e subclasse de int em Python — sem o guard em converter_numericos,
        # esta coluna viraria Int64 [1, 0, <NA>] em vez de continuar True/False/None.
        df = pd.DataFrame({"opcao_pelo_simples": [True, False, None]})

        resultado = cleaning.converter_numericos(df)

        self.assertEqual(resultado["opcao_pelo_simples"].tolist(), [True, False, None])


class ValidarMunicipioUfTest(unittest.TestCase):
    """Cruzamento de dados: codigo IBGE de municipio informado por outra fonte x base oficial."""

    @patch("app.pipeline.ingestion.ibge.requests.get")
    def test_sinaliza_divergencia_entre_uf_informada_e_uf_oficial_do_municipio(self, mock_get) -> None:
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
        mock_get.return_value = _fake_response([abaiara])

        municipios = cleaning.limpar_ibge_municipios(ibge.listar_municipios("CE"))

        # simula uma tabela de contratacoes ja limpa (PNCP), com o codigo IBGE do orgao
        contratacoes = pd.DataFrame(
            [
                # codigo bate com Abaiara/CE, UF informada tambem e CE -> confere
                {"numero_controle_pncp": "A", "unidade_orgao_codigo_ibge": 2301000, "unidade_orgao_uf_sigla": "CE"},
                # mesmo codigo (Abaiara/CE), mas UF informada errada -> nao confere
                {"numero_controle_pncp": "B", "unidade_orgao_codigo_ibge": 2301000, "unidade_orgao_uf_sigla": "SP"},
                # codigo que nao existe na base do IBGE -> nao verificavel (None, nao False)
                {"numero_controle_pncp": "C", "unidade_orgao_codigo_ibge": 9999999, "unidade_orgao_uf_sigla": "CE"},
            ]
        )

        resultado = cleaning.validar_municipio_uf(
            contratacoes,
            municipios,
            coluna_codigo_municipio="unidade_orgao_codigo_ibge",
            coluna_uf="unidade_orgao_uf_sigla",
        )

        coluna = "unidade_orgao_codigo_ibge_uf_confere"
        por_id = resultado.set_index("numero_controle_pncp")[coluna]
        self.assertTrue(por_id["A"])
        self.assertFalse(por_id["B"])
        self.assertIsNone(por_id["C"])


if __name__ == "__main__":
    unittest.main()

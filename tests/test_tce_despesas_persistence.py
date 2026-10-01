from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from sqlalchemy.dialects import postgresql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from app.core.models import TceAnulacaoEmpenho, TceDespesaIngestionRun, TceEmpenho
from app.pipeline.persistence import tce_despesas


CHAVE_EMPENHO = "010|202500|17|01|2025-01-31|31010002"


def _empenho(**alteracoes):
    registro = {
        "codigo_municipio_tce": "010",
        "competencia": "2025-01",
        "chave_empenho": CHAVE_EMPENHO,
        "exercicio_orcamento": 202500,
        "codigo_orgao": "17",
        "codigo_unidade_orcamentaria": "01",
        "data_empenho": "2025-01-31",
        "numero_empenho": "31010002",
        "codigo_natureza_despesa": "44905200",
        "codigo_elemento_despesa": "52",
        "natureza_considerada": True,
        "valor_empenhado_centavos": 1_111_500,
        "tipo_documento_fornecedor": "CNPJ",
        "documento_fornecedor": "10496308000123",
        "cnpj_fornecedor": "10496308000123",
        "cpf_fornecedor": None,
        "nome_fornecedor": "Fornecedor",
        "municipio_fornecedor_informado": "Quixeramobim",
        "uf_fornecedor_informada": "CE",
        "estado_empenho": "OR",
        "numero_nota_anulacao_informado": None,
        "numero_empenho_substituto": None,
        "numero_contrato": "006/2025",
        "numero_licitacao": "DE 001/2025",
        "payload_bruto": {"numero_empenho": "31010002"},
    }
    registro.update(alteracoes)
    return registro


def _anulacao(**alteracoes):
    registro = {
        "codigo_municipio_tce": "010",
        "competencia": "2025-02",
        "chave_empenho": CHAVE_EMPENHO,
        "chave_anulacao": f"{CHAVE_EMPENHO}|2025-02-10|10020001",
        "data_empenho": "2025-01-31",
        "numero_empenho": "31010002",
        "numero_anulacao": "10020001",
        "data_anulacao": "2025-02-10",
        "modalidade_anulacao": "T",
        "descricao_anulacao": "Anulação posterior",
        "valor_anulacao_centavos": 5_950,
        "payload_bruto": {"numero_nota_anulacao": "10020001"},
    }
    registro.update(alteracoes)
    return registro


class _SessaoFalsa:
    def __init__(self) -> None:
        self.adicionados = []
        self.flushes = []
        self._proximo_id = 100

    def add(self, objeto) -> None:
        self.adicionados.append(objeto)

    def add_all(self, objetos) -> None:
        self.adicionados.extend(list(objetos))

    def flush(self) -> None:
        for objeto in self.adicionados:
            if isinstance(objeto, TceDespesaIngestionRun) and objeto.id is None:
                objeto.id = self._proximo_id
                self._proximo_id += 1
        self.flushes.append(
            [
                (objeto.id, objeto.status)
                for objeto in self.adicionados
                if isinstance(objeto, TceDespesaIngestionRun)
            ]
        )


class ValidacaoLoteTceTest(unittest.TestCase):
    def _validar(self, empenhos=(), anulacoes=(), **referencias):
        return tce_despesas.validar_lote_tce(
            codigo_municipio_tce="010",
            competencia="2025-01" if empenhos else "2025-02",
            empenhos=empenhos,
            anulacoes=anulacoes,
            **referencias,
        )

    def test_duplicata_identica_e_deduplicada(self) -> None:
        registro = _empenho()

        lote = self._validar(empenhos=[registro, dict(registro)])

        self.assertTrue(lote.valido)
        self.assertEqual(len(lote.empenhos), 1)

    def test_duplicata_conflitante_rejeita_lote(self) -> None:
        lote = self._validar(
            empenhos=[_empenho(), _empenho(valor_empenhado_centavos=10)],
        )

        self.assertFalse(lote.valido)
        self.assertIn("DUPLICIDADE_CONFLITANTE", {p["codigo"] for p in lote.problemas})

    def test_anulacao_orfa_rejeita_competencia(self) -> None:
        lote = self._validar(anulacoes=[_anulacao()])

        self.assertFalse(lote.valido)
        self.assertIn("ANULACAO_ORFA", {p["codigo"] for p in lote.problemas})

    def test_anulacao_posterior_corrige_empenho_publicado_em_janeiro(self) -> None:
        lote = self._validar(
            anulacoes=[_anulacao()],
            valores_empenhos_publicados={CHAVE_EMPENHO: 1_111_500},
        )

        self.assertTrue(lote.valido)

    def test_soma_de_anulacoes_nao_pode_deixar_saldo_negativo(self) -> None:
        lote = self._validar(
            anulacoes=[_anulacao(valor_anulacao_centavos=60_000)],
            valores_empenhos_publicados={CHAVE_EMPENHO: 100_000},
            anulacoes_publicadas_centavos={CHAVE_EMPENHO: 50_000},
        )

        self.assertFalse(lote.valido)
        self.assertIn("VALOR_LIQUIDO_NEGATIVO", {p["codigo"] for p in lote.problemas})

    def test_reprocessar_janeiro_respeita_anulacao_posterior_ja_publicada(self) -> None:
        lote = self._validar(
            empenhos=[_empenho(valor_empenhado_centavos=5_000)],
            anulacoes_publicadas_centavos={CHAVE_EMPENHO: 5_950},
        )

        self.assertFalse(lote.valido)
        self.assertIn("VALOR_LIQUIDO_NEGATIVO", {p["codigo"] for p in lote.problemas})


class PublicacaoLoteTceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.agora = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        self.sessao = _SessaoFalsa()

    @contextmanager
    def _contexto_sessao(self):
        yield self.sessao

    def _patches(self, lote_anterior):
        return (
            patch("app.pipeline.persistence.tce_despesas.database.init_db"),
            patch(
                "app.pipeline.persistence.tce_despesas.orm.main_session",
                side_effect=self._contexto_sessao,
            ),
            patch("app.pipeline.persistence.tce_despesas._agora_utc", return_value=self.agora),
            patch("app.pipeline.persistence.tce_despesas._bloquear_particao"),
            patch("app.pipeline.persistence.tce_despesas._remover_lotes_expirados_session"),
            patch(
                "app.pipeline.persistence.tce_despesas._lote_publicado_atual",
                return_value=lote_anterior,
            ),
            patch(
                "app.pipeline.persistence.tce_despesas._carregar_referencias_publicadas",
                return_value=({}, {}),
            ),
        )

    def test_publicacao_valida_substitui_lote_e_retem_anterior_sete_dias(self) -> None:
        anterior = TceDespesaIngestionRun(
            id=5,
            codigo_municipio_tce="010",
            competencia="2025-01",
            status=tce_despesas.STATUS_PUBLICADO,
            iniciado_em=self.agora - timedelta(days=1),
            quantidade_empenhos=1,
            quantidade_anulacoes=0,
            problemas=[],
        )
        patches = self._patches(anterior)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            resultado = tce_despesas.publicar_lote_tce(
                codigo_municipio_tce="010",
                competencia="2025-01",
                empenhos=[_empenho()],
                anulacoes=[],
            )

        self.assertEqual(resultado.status, tce_despesas.STATUS_PUBLICADO)
        self.assertEqual(resultado.run_anterior_id, 5)
        self.assertEqual(anterior.status, tce_despesas.STATUS_SUBSTITUIDO)
        self.assertEqual(anterior.substituido_em, self.agora)
        self.assertEqual(anterior.expira_em, self.agora + timedelta(days=7))
        self.assertEqual(len([o for o in self.sessao.adicionados if isinstance(o, TceEmpenho)]), 1)
        self.assertEqual(self.sessao.flushes[-2], [(resultado.run_id, "EM_VALIDACAO")])
        self.assertEqual(self.sessao.flushes[-1], [(resultado.run_id, "PUBLICADO")])

    def test_lote_invalido_e_rejeitado_sem_alterar_publicado(self) -> None:
        anterior = TceDespesaIngestionRun(
            id=5,
            codigo_municipio_tce="010",
            competencia="2025-02",
            status=tce_despesas.STATUS_PUBLICADO,
            iniciado_em=self.agora - timedelta(days=1),
            quantidade_empenhos=0,
            quantidade_anulacoes=1,
            problemas=[],
        )
        patches = self._patches(anterior)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            resultado = tce_despesas.publicar_lote_tce(
                codigo_municipio_tce="010",
                competencia="2025-02",
                empenhos=[],
                anulacoes=[_anulacao()],
            )

        self.assertEqual(resultado.status, tce_despesas.STATUS_REJEITADO)
        self.assertEqual(anterior.status, tce_despesas.STATUS_PUBLICADO)
        self.assertIn("ANULACAO_ORFA", {p["codigo"] for p in resultado.problemas})
        self.assertFalse(
            any(isinstance(o, (TceEmpenho, TceAnulacaoEmpenho)) for o in self.sessao.adicionados)
        )
        run_rejeitado = next(
            o for o in self.sessao.adicionados if isinstance(o, TceDespesaIngestionRun)
        )
        self.assertEqual(run_rejeitado.expira_em, self.agora + timedelta(days=7))

    def test_limpeza_exclui_somente_versoes_inativas_expiradas(self) -> None:
        class Resultado:
            rowcount = 3

        class Sessao:
            def __init__(self):
                self.comando = None

            def execute(self, comando):
                self.comando = comando
                return Resultado()

        sessao = Sessao()

        removidos = tce_despesas._remover_lotes_expirados_session(sessao, self.agora)

        sql = str(
            sessao.comando.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertEqual(removidos, 3)
        self.assertIn("'SUBSTITUIDO'", sql)
        self.assertIn("'REJEITADO'", sql)
        self.assertNotIn("'PUBLICADO'", sql)

    def test_lock_e_compartilhado_entre_competencias_do_municipio(self) -> None:
        class Sessao:
            def __init__(self):
                self.parametros = []

            def execute(self, comando, parametros):
                self.parametros.append(parametros)

        sessao = Sessao()

        tce_despesas._bloquear_particao(sessao, "010", "2025-01")
        tce_despesas._bloquear_particao(sessao, "010", "2025-02")

        self.assertEqual(
            sessao.parametros,
            [{"escopo": "tce-despesas:010"}, {"escopo": "tce-despesas:010"}],
        )


class LeituraAnaliticaTceTest(unittest.TestCase):
    def test_lista_instantes_das_competencias_publicadas(self) -> None:
        publicado_em = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)

        class Resultado:
            def all(self):
                return [("2026-09", publicado_em)]

        class Sessao:
            comando = None

            def execute(self, comando):
                self.comando = comando
                return Resultado()

        sessao = Sessao()

        @contextmanager
        def contexto():
            yield sessao

        with (
            patch("app.pipeline.persistence.tce_despesas.database.init_db"),
            patch("app.pipeline.persistence.tce_despesas.orm.main_session", side_effect=contexto),
        ):
            resultado = tce_despesas.listar_publicacoes_competencias(
                codigo_municipio_tce="010",
                competencias=["2026-09", "2026-09"],
            )

        sql = str(
            sessao.comando.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertEqual(resultado, {"2026-09": publicado_em})
        self.assertIn("status = 'PUBLICADO'", sql)
        self.assertIn("competencia IN ('2026-09')", sql)

    def test_le_empenho_publicado_e_desconta_anulacoes_de_qualquer_competencia(self) -> None:
        registro = {
            "chave_empenho": CHAVE_EMPENHO,
            "exercicio_orcamento": 202500,
            "codigo_orgao": "17",
            "codigo_unidade_orcamentaria": "01",
            "data_empenho": date(2025, 1, 31),
            "numero_empenho": "31010002",
            "codigo_natureza_despesa": "44905200",
            "codigo_elemento_despesa": "52",
            "valor_empenhado_centavos": 1_111_500,
            "valor_anulado_centavos": 5_950,
            "valor_liquido_centavos": 1_105_550,
            "tipo_documento_fornecedor": "1",
            "documento_fornecedor": "10496308000123",
            "cnpj_fornecedor": "10496308000123",
            "cpf_fornecedor": None,
            "nome_fornecedor": "Fornecedor",
            "municipio_fornecedor_informado": "Quixeramobim",
            "uf_fornecedor_informada": "CE",
            "estado_empenho": "OR",
        }

        class Resultado:
            def mappings(self):
                return self

            def all(self):
                return [registro]

        class Sessao:
            comando = None

            def execute(self, comando):
                self.comando = comando
                return Resultado()

        sessao = Sessao()

        @contextmanager
        def contexto():
            yield sessao

        with (
            patch("app.pipeline.persistence.tce_despesas.database.init_db"),
            patch("app.pipeline.persistence.tce_despesas.orm.main_session", side_effect=contexto),
        ):
            resultado = tce_despesas.listar_empenhos_liquidos_publicados(
                codigo_municipio_tce="010",
                data_inicial=date(2025, 1, 1),
                data_final=date(2025, 1, 31),
            )

        sql = str(
            sessao.comando.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertEqual(resultado[0]["data_empenho"], "2025-01-31")
        self.assertEqual(resultado[0]["valor_liquido_centavos"], 1_105_550)
        self.assertIn("tce_anulacoes_empenhos", sql)
        self.assertIn("natureza_considerada IS true", sql)
        self.assertNotIn("data_anulacao <=", sql)


if __name__ == "__main__":
    unittest.main()

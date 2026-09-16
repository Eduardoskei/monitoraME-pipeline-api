"""Validacoes de integracao e falhas do spike, sem chamadas externas."""
from collections import Counter
from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from scripts import spike_pncp_porte as spike


class PncpEnvironmentTests(unittest.TestCase):
    def test_base_padrao(self):
        with patch.dict(spike.os.environ, {}, clear=True):
            self.assertEqual(spike.pncp_base("PNCP_TEST_URL", "https://example.test/api"), "https://example.test/api/v1")

    def test_base_configurada_e_normalizada(self):
        for value in ("https://configured.test/api", "https://configured.test/api/", " https://configured.test/api/v1/ "):
            with self.subTest(value=value), patch.dict(spike.os.environ, {"PNCP_TEST_URL": value}):
                self.assertEqual(spike.pncp_base("PNCP_TEST_URL", "https://default.test"), "https://configured.test/api/v1")

    def test_base_vazia_rejeitada(self):
        with patch.dict(spike.os.environ, {"PNCP_TEST_URL": "  "}):
            with self.assertRaises(ValueError):
                spike.pncp_base("PNCP_TEST_URL", "https://default.test")


class SpikeValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)

    def response(self, status, payload=None):
        response = Mock(status_code=status)
        response.json.return_value = payload
        if status >= 400:
            response.raise_for_status.side_effect = requests.HTTPError(response=response)
        return response

    def test_catalogo_do_spike_compativel_com_pipeline(self):
        from app.pipeline.cleaners.pncp import limpar_resultados
        catalog = json.loads((Path(__file__).resolve().parents[1] /
                              "docs/spikes/pncp-porte/catalogo_portes.json").read_text(encoding="utf-8"))
        labels = {"MEI": "MEI", "ME": "ME", "EPP": "EPP", "DEMAIS": "Demais Empresas",
                  "NAO_SE_APLICA": "Não se aplica", "NAO_INFORMADO": "Não informado"}
        records = [{"porteFornecedorId": entry["id"]} for entry in catalog]
        cleaned = limpar_resultados(records)
        for record, normalized in zip(records, cleaned["porte_fornecedor_padronizado"]):
            with self.subTest(code=record["porteFornecedorId"]):
                self.assertEqual(spike.classify(record)[0], labels[normalized])

    def test_cache_evita_requisicao_repetida(self):
        client = spike.Client(self.output)
        with patch.object(spike.requests, "get", return_value=self.response(200, [{"id": 1}])) as get:
            first = client.get("https://example.test/items", {"pagina": 1})
            self.assertEqual(first, client.get("https://example.test/items", {"pagina": 1}))
            get.assert_called_once()
        saved = json.loads(next(client.cache.glob("*.json")).read_text(encoding="utf-8"))
        self.assertIn("pagina=1", saved["url"])
        self.assertEqual(saved["status"], 200)

    def test_204_e_lista_vazia_e_nao_tenta_ler_json(self):
        response = self.response(204)
        with patch.object(spike.requests, "get", return_value=response):
            self.assertEqual(spike.Client(self.output).get("https://example.test/items"), [])
        response.json.assert_not_called()

    def test_429_repete_com_espera_e_nao_salva_erro(self):
        with patch.object(spike.requests, "get", side_effect=[
            self.response(429), self.response(200, [{"id": 1}])
        ]) as get, patch.object(spike.time, "sleep") as sleep:
            result = spike.Client(self.output).get("https://example.test/items")
        self.assertEqual(result, [{"id": 1}])
        self.assertEqual(get.call_count, 2)
        self.assertTrue(any(call.args[0] > 20 for call in sleep.call_args_list))
        self.assertEqual(len(list((self.output / "raw").glob("*.json"))), 1)

    def test_404_nao_e_cacheado_como_lista_vazia(self):
        with patch.object(spike.requests, "get", return_value=self.response(404)), patch.object(spike.time, "sleep"):
            with self.assertRaises(requests.HTTPError):
                spike.Client(self.output).get("https://example.test/items")
        self.assertEqual(list((self.output / "raw").glob("*.json")), [])

    def test_timeout_esgota_tentativas_e_propaga(self):
        with patch.object(spike.requests, "get", side_effect=requests.Timeout) as get, patch.object(spike.time, "sleep"):
            with self.assertRaises(requests.Timeout):
                spike.Client(self.output).get("https://example.test/items")
        self.assertEqual(get.call_count, 5)

    def test_pagina_vazia_antes_do_total_nao_pode_ocultar_truncamento(self):
        client = object.__new__(spike.Client)
        for empty in ({"data": [], "totalPaginas": 3}, []):
            with self.subTest(resposta=empty):
                client.get = Mock(side_effect=[
                    {"data": [{"id": 1}], "totalPaginas": 3}, empty,
                ])
                with self.assertRaises(ValueError):
                    client.pages("https://example.test/items")

    def test_lista_paginada_sem_metadados(self):
        client = object.__new__(spike.Client)
        client.get = Mock(side_effect=[[{"id": i} for i in range(50)], [{"id": 50}]])
        self.assertEqual(len(client.pages("https://example.test/items")), 51)
        self.assertEqual(client.get.call_args_list[1].args[1]["pagina"], 2)

    def test_sorteio_reproduzivel_independente_da_ordem_e_duplicatas(self):
        purchases = [{"numeroControlePNCP": str(i)} for i in range(20)]
        client = Mock()
        client.pages.return_value = purchases
        cell = ("2304400", "2025-01", 6)
        first = spike.collect_frame(client, cell, 5, 42)
        client.pages.return_value = list(reversed(purchases)) + [purchases[0]]
        self.assertEqual(first, spike.collect_frame(client, cell, 5, 42))
        self.assertEqual(first[0]["compras_universo"], 20)
        self.assertEqual(first[0]["fracao_amostral_compras"], 0.25)

    def test_estrato_sem_compras(self):
        client = Mock()
        client.pages.return_value = []
        audit, selected = spike.collect_frame(client, ("2304400", "2025-01", 6), 2, 42)
        self.assertEqual(selected, [])
        self.assertIsNone(audit["fracao_amostral_compras"])
        self.assertEqual(audit["status"], "ok")

    def test_grupo_planejado_vazio_tem_seis_categorias_sem_percentual(self):
        rows = spike.aggregate([], ["municipio"], [("Amontada",)])
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row["quantidade"] == 0 and row["percentual"] is None for row in rows))

    def test_relatorio_sem_resultados_e_com_falha_nao_inventa_cobertura(self):
        manifest = {"executado_em": "teste", "seed": 42, "por_estrato": 2,
                    "max_itens": 5, "compras": [], "erros": [{"erro": "timeout"}]}
        spike.report(self.output, [], manifest)
        text = (self.output / "README.md").read_text(encoding="utf-8")
        self.assertIn("cobertura indeterminada", text)
        self.assertIn("Coleta incompleta", text)
        with (self.output / "por_municipio_periodo.csv").open(encoding="utf-8-sig") as stream:
            groups = list(csv.DictReader(stream))
        self.assertEqual(len(groups), 4 * 3 * 6)
        self.assertTrue(all(row["percentual"] == "" for row in groups))

    def test_argumentos_invalidos_falham_antes_da_coleta(self):
        with patch("sys.argv", ["spike", "--output", str(self.output), "--max-itens", "0"]),              patch.object(spike, "Client") as client, patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as result:
                spike.main()
        self.assertEqual(result.exception.code, 2)
        client.assert_not_called()

    def test_reproduz_snapshot_real_sem_rede(self):
        source = Path(__file__).resolve().parents[1] / "docs/spikes/pncp-porte"
        if not (source / "raw").exists():
            self.skipTest("Cache local do spike nao disponivel; raw/ nao e versionado")
        real_client = spike.Client
        def cached_client(output):
            client = real_client(output)
            client.cache = source / "raw"
            return client
        with patch.object(spike, "Client", side_effect=cached_client),              patch.object(spike.requests, "get", side_effect=AssertionError("Cache incompleto: acesso a rede")),              patch("sys.argv", ["spike", "--output", str(self.output)]), redirect_stdout(io.StringIO()):
            spike.main()
        for original in source.glob("*.csv"):
            with self.subTest(arquivo=original.name):
                self.assertEqual(original.read_bytes(), (self.output / original.name).read_bytes())
        for name in ("compras_sorteadas.json", "catalogo_portes.json", "manifesto.json"):
            expected = json.loads((source / name).read_text(encoding="utf-8"))
            actual = json.loads((self.output / name).read_text(encoding="utf-8"))
            if name == "manifesto.json":
                expected.pop("executado_em")
                actual.pop("executado_em")
            self.assertEqual(actual, expected)
        expected_text = (source / "README.md").read_text(encoding="utf-8").splitlines()
        actual_text = (self.output / "README.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual([line for line in actual_text if not line.startswith("Execução UTC:")],
                         [line for line in expected_text if not line.startswith("Execução UTC:")])

    def test_snapshot_confere_com_respostas_brutas_e_denominadores_locais(self):
        source = Path(__file__).resolve().parents[1] / "docs/spikes/pncp-porte"
        if not (source / "raw").exists():
            self.skipTest("Cache local do spike nao disponivel")
        with (source / "resultados.csv").open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        raw = [json.loads(path.read_text(encoding="utf-8")) for path in (source / "raw").glob("*.json")]
        active = [row for entry in raw if "/resultados" in entry["url"] for row in entry["payload"]
                  if not row.get("dataCancelamento") and row.get("situacaoCompraItemResultadoId") != 2]
        self.assertEqual(Counter(str(row["porteFornecedorId"]) for row in active),
                         Counter(row["porte_id_original"] for row in rows))
        self.assertEqual(len(rows), len({(row["compra"], row["item"], row["resultado"]) for row in rows}))
        for filename, fields in {
            "municipio": ["municipio_ibge", "municipio"], "periodo": ["periodo"],
            "municipio_periodo": ["municipio_ibge", "municipio", "periodo"],
            "orgao": ["orgao"], "modalidade": ["modalidade"], "fornecedor": ["fornecedor"],
            "tipo_pessoa": ["tipo_pessoa"],
        }.items():
            with (source / f"por_{filename}.csv").open(encoding="utf-8-sig") as stream:
                grouped = list(csv.DictReader(stream))
            self.assertEqual(sum(int(row["quantidade"]) for row in grouped), len(rows))
            for entry in grouped:
                matches = [row for row in rows if all(row[field] == entry[field] for field in fields)]
                count = sum(row["porte"] == entry["porte"] for row in matches)
                self.assertEqual(int(entry["total"]), len(matches))
                self.assertEqual(int(entry["quantidade"]), count)
                if matches:
                    self.assertAlmostEqual(float(entry["percentual"]), 100 * count / len(matches), places=3)
                else:
                    self.assertEqual(entry["percentual"], "")


if __name__ == "__main__":
    unittest.main()

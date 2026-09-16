import itertools
import io
import json
import math
from pathlib import Path
import statistics
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from scripts import spike_pncp_representatividade as survey


def unit(values, indices=None, *, pi=1, group="h", city="A"):
    if indices is None:
        indices = range(len(values))
    obs = [{"total": 1, "contagens": {c: int(c == ("Não informado" if values[i] else "ME"))
                                    for c in survey.CATEGORIES}} for i in indices]
    return {"M": len(values), "m": len(obs), "pi_compra": pi, "estrato": group,
            "observacoes": obs, "municipio_ibge": city}


class SamplingDesignTests(unittest.TestCase):
    def test_dimensionamento_alocacao_e_probabilidades_positivas(self):
        sizes = {"A": 10000, "B": 1000, "C": 1, "D": 0}
        counts, plan = survey.allocation(sizes)
        self.assertEqual(plan["n0_aas"], 385)
        self.assertEqual(sum(counts.values()), plan["n_planejado"])
        for key, size in sizes.items():
            self.assertLessEqual(counts[key], size)
            self.assertGreaterEqual(counts[key], min(2, size))
        self.assertGreater(counts["A"], counts["B"])
        self.assertEqual(counts["C"], 1)
        self.assertEqual(counts["D"], 0)

    def test_populacao_pequena_vira_censo(self):
        counts, plan = survey.allocation({"A": 1, "B": 2, "C": 0})
        self.assertEqual(counts, {"A": 1, "B": 2, "C": 0})
        self.assertEqual(plan["n_planejado"], 3)

    def test_margem_invalida_e_populacao_vazia(self):
        for margin in (0, 1, -0.1):
            with self.assertRaises(ValueError):
                survey.allocation({"A": 10}, margin)
        self.assertEqual(survey.allocation({"A": 0})[1]["n_planejado"], 0)

    def test_paginacao_detecta_total_alterado_ou_pagina_incompleta(self):
        client = Mock()
        params = survey.frame_params(1, 6)
        client.get.return_value = {"data": [], "totalRegistros": 120, "numeroPagina": 1, "totalPaginas": 3}
        with self.assertRaisesRegex(ValueError, "incompleta"):
            survey.checked_page(client, params, 1, 120)
        with self.assertRaisesRegex(ValueError, "mudou"):
            survey.checked_page(client, params, 1, 121)

    def test_censo_por_posicao_inclui_paginas_intermediaria_e_final(self):
        client = Mock()
        publications = [{"numeroControlePNCP": str(i), "unidadeOrgao": {"ufSigla": "CE"},
                         "modalidadeId": 6, "dataPublicacaoPncp": "2025-01-01"} for i in range(120)]
        def get(url, params):
            page = params["pagina"]
            return {"data": publications[(page-1)*50:page*50], "totalRegistros": 120,
                    "numeroPagina": page, "totalPaginas": 3}
        client.get.side_effect = get
        selected = survey.select_purchases(client, {"id": "h", "N": 120, "modalidade": 6,
                                                   "params": survey.frame_params(1, 6)}, 120, 42)
        self.assertEqual([r["posicao_zero_based"] for r in selected], list(range(120)))
        self.assertTrue(all(r["pi_compra"] == 1 for r in selected))
        self.assertEqual(client.get.call_count, 3)

    def test_censo_tem_estimativa_exata_e_variancia_zero(self):
        units = [unit([0, 1]), unit([1, 1])]
        result = survey.estimate(units, [{"id": "h", "N": 2, "n": 2}], "Não informado")
        self.assertEqual(result["percentual"], 75)
        self.assertEqual(result["erro_padrao_pp"], 0)
        self.assertEqual(result["ic95_inferior"], 75)
        self.assertEqual(result["status_ic"], "censo")

    def test_pesos_corrigem_alocacao_desproporcional(self):
        units = [unit([1], pi=0.02, group="A"), unit([1], pi=0.02, group="A"),
                 unit([0], group="B"), unit([0], group="B")]
        cells = [{"id": "A", "N": 100, "n": 2}, {"id": "B", "N": 2, "n": 2}]
        result = survey.estimate(units, cells, "Não informado")
        self.assertAlmostEqual(result["percentual"], 100 * 100 / 102)
        self.assertEqual(result["quantidade_amostral"], 2)
        self.assertEqual(result["total_amostral"], 4)

    def test_variancia_dois_estagios_confere_com_enumeracao_exata(self):
        # Todos os 27 sorteios possiveis: 2 de 3 compras; 2 de 3 itens em cada compra.
        population = [[0, 0, 1], [0, 1, 1], [1, 1, 1]]
        estimates, estimated_variances = [], []
        item_samples = list(itertools.combinations(range(3), 2))
        for psus in itertools.combinations(range(3), 2):
            for samples in itertools.product(item_samples, repeat=2):
                units = [unit(population[i], positions, pi=2/3) for i, positions in zip(psus, samples)]
                result = survey.estimate(units, [{"id": "h", "N": 3, "n": 2}], "Não informado")
                estimates.append(result["percentual"] / 100)
                estimated_variances.append((result["erro_padrao_pp"] / 100) ** 2)
        self.assertEqual(len(estimates), 27)
        self.assertAlmostEqual(statistics.mean(estimates), 2/3, places=12)
        self.assertAlmostEqual(statistics.mean(estimated_variances),
                               statistics.pvariance(estimates), places=12)

    def test_dominio_preserva_compras_de_fora_no_desenho(self):
        units = [unit([1], pi=0.5, city="A"), unit([0], pi=0.5, city="B")]
        result = survey.estimate(units, [{"id": "h", "N": 4, "n": 2}], "Não informado", {"municipio_ibge": "A"})
        self.assertEqual(result["total_amostral"], 1)
        self.assertEqual(result["total_estimado_resultados"], 2)
        self.assertEqual(result["percentual"], 100)

    def test_sem_eventos_nao_inventa_intervalo_zero(self):
        result = survey.estimate([unit([0], pi=0.5), unit([0], pi=0.5)],
                                 [{"id": "h", "N": 4, "n": 2}], "Não informado")
        self.assertEqual(result["percentual"], 0)
        self.assertIsNone(result["ic95_superior"])
        self.assertEqual(result["status_ic"], "sem_eventos_para_estimar")

    def test_sem_resultados_e_sem_replicacao(self):
        empty = unit([])
        result = survey.estimate([empty], [{"id": "h", "N": 1, "n": 1}], "Não informado")
        self.assertIsNone(result["percentual"])
        with self.assertRaisesRegex(ValueError, "replicacao"):
            survey.estimate([unit([0, 1], [0])], [{"id": "h", "N": 1, "n": 1}], "Não informado")

    def test_coleta_completa_simulada_com_revalidacao(self):
        class FakeClient:
            def __init__(self, output):
                pass
            def get(self, url, params=None):
                if url.endswith("/modalidades"):
                    return [{"id": 6, "nome": "Pregão"}]
                if url.endswith("/portes-empresa"):
                    return [{"id": 6, "nome": "MEI"}]
                if "/publicacao" in url:
                    month = int(params["dataInicial"][4:6])
                    data = [{"numeroControlePNCP": f"compra-{month}-{i}",
                             "dataPublicacaoPncp": f"2025-{month:02d}-01",
                             "unidadeOrgao": {"ufSigla": "CE", "codigoIbge": str(i), "municipioNome": str(i)},
                             "orgaoEntidade": {"cnpj": "123"}, "modalidadeId": 6,
                             "anoCompra": 2025, "sequencialCompra": month*10+i} for i in range(2)]
                    return {"data": data, "totalRegistros": 2, "numeroPagina": 1, "totalPaginas": 1}
                if url.endswith("/itens"):
                    return [{"numeroItem": 1}, {"numeroItem": 2}]
                if url.endswith("/resultados"):
                    item = int(url.split("/")[-2])
                    return [{"sequencialResultado": 1, "porteFornecedorId": 5 if item == 1 else 1,
                             "niFornecedor": "456", "tipoPessoa": "PJ"}]
                raise AssertionError(url)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(survey, "Client", FakeClient),                  patch("sys.argv", ["survey", "--output", directory]), redirect_stdout(io.StringIO()):
                survey.main()
            manifest = json.loads((Path(directory) / "manifesto.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "concluido")
            self.assertEqual(len(manifest["estratos_revalidados"]), 4)
            self.assertEqual(manifest["resumo"]["resultados_ativos"], 16)
            self.assertEqual(manifest["resumo"]["nao_informado"]["percentual"], 50)
            self.assertEqual(manifest["resumo"]["nao_informado"]["ic95_inferior"], 50)



class FailureHandlingTests(unittest.TestCase):
    def test_limite_de_tentativas_e_timeout_configurados(self):
        import requests
        from scripts.spike_pncp_porte import Client
        with tempfile.TemporaryDirectory() as directory:
            client = Client(Path(directory), max_attempts=2, timeout=(1, 2))
            with patch("scripts.spike_pncp_porte.requests.get", side_effect=requests.ReadTimeout) as get,                  patch("scripts.spike_pncp_porte.time.sleep"):
                with self.assertRaises(requests.ReadTimeout):
                    client.get("https://example.test")
            self.assertEqual(get.call_count, 2)
            self.assertEqual(get.call_args.kwargs["timeout"], (1, 2))

    def test_cancelamento_impede_nova_tentativa(self):
        import requests
        from scripts.spike_pncp_porte import Client
        with tempfile.TemporaryDirectory() as directory:
            client = Client(Path(directory))
            def timeout(*args, **kwargs):
                client.cancelled = True
                raise requests.ReadTimeout()
            with patch("scripts.spike_pncp_porte.requests.get", side_effect=timeout) as get,                  patch("scripts.spike_pncp_porte.time.sleep"):
                with self.assertRaisesRegex(RuntimeError, "cancelada"):
                    client.get("https://example.test")
            get.assert_called_once()

    def test_falha_real_da_fonte_gera_relatorio_incompleto(self):
        import requests
        class FailedClient:
            def __init__(self, output):
                self.cancelled = False
            def get(self, url, params=None):
                if url.endswith("/modalidades"):
                    return [{"id": 6, "nome": "Pregão"}]
                if url.endswith("/portes-empresa"):
                    return []
                raise requests.ReadTimeout("Fonte indisponivel")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "diagnostico_api.json").write_text(json.dumps({
                "status_http": 500, "mensagem": "Erro na comunicação com o banco de dados"
            }), encoding="utf-8-sig")
            with patch.object(survey, "Client", FailedClient),                  patch("sys.argv", ["survey", "--output", directory]), redirect_stdout(io.StringIO()):
                with self.assertRaises(requests.ReadTimeout):
                    survey.main()
            manifest = json.loads((output / "manifesto.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "incompleto")
            self.assertEqual(manifest["erros"][0]["tipo"], "ReadTimeout")
            text = (output / "README.md").read_text(encoding="utf-8")
            self.assertIn("PEN-005 ainda não concluído", text)
            self.assertIn("HTTP 500", text)
            self.assertFalse((output / "estimativas_ponderadas.csv").exists())

if __name__ == "__main__":
    unittest.main()

import unittest
from scripts.spike_pncp_porte import classify, distribution, aggregate, Client


class PorteSpikeTests(unittest.TestCase):
    def test_catalogo_seis_categorias(self):
        for code, expected in [(6, "MEI"), (1, "ME"), (2, "EPP"),
                               (3, "Demais Empresas"), (4, "Não se aplica"), (5, "Não informado")]:
            self.assertEqual(classify({"porteFornecedorId": code})[0], expected)

    def test_ausentes_invalidos_e_nao_aplicavel_separados(self):
        for value in (None, "", "  "):
            self.assertEqual(classify({"porteFornecedorId": value}), ("Não informado", "ausente_nulo_vazio"))
        for value in (0, 7, True, 1.5, [], {}):
            self.assertEqual(classify({"porteFornecedorId": value}), ("Não informado", "codigo_invalido"))
        self.assertEqual(classify({"porteFornecedorId": 4}), ("Não se aplica", "valido"))

    def test_exemplo_solicitado(self):
        counts = [("MEI", 800), ("ME", 2400), ("EPP", 1800), ("Demais Empresas", 2700),
                  ("Não se aplica", 500), ("Não informado", 1800)]
        rows = [{"porte": cat} for cat, count in counts for _ in range(count)]
        actual = distribution(rows)
        self.assertEqual([r["percentual"] for r in actual], [8, 24, 18, 27, 5, 18])
        self.assertEqual(sum(r["quantidade"] for r in actual), 10000)

    def test_grupo_usa_denominador_local(self):
        rows = [{"municipio": "A", "porte": "ME"}, {"municipio": "B", "porte": "Não informado"}]
        missing = [r for r in aggregate(rows, ["municipio"]) if r["porte"] == "Não informado"]
        self.assertEqual([r["percentual"] for r in missing], [0, 100])

    def test_vazio_nao_e_zero_porcento(self):
        self.assertTrue(all(r["percentual"] is None for r in distribution([])))

    def test_paginacao_e_resposta_invalida(self):
        client = object.__new__(Client)
        pages = iter([{"data": [{"id": 1}], "totalPaginas": 2},
                      {"data": [{"id": 2}], "totalPaginas": 2}])
        client.get = lambda *args: next(pages)
        self.assertEqual(client.pages("url"), [{"id": 1}, {"id": 2}])
        client.get = lambda *args: {"unexpected": []}
        with self.assertRaises(ValueError):
            client.pages("url")




class CollectionSpikeTests(unittest.TestCase):
    def test_cancelamento_duplicata_e_item_vazio_nao_viram_porte_ausente(self):
        from scripts.spike_pncp_porte import collect_purchase
        class FakeClient:
            def pages(self, url):
                return [{"numeroItem": 1}, {"numeroItem": 2}]
            def get(self, url):
                if "/itens/2/" in url:
                    return []
                return [{"sequencialResultado": 1, "porteFornecedorId": 1},
                        {"sequencialResultado": 1, "porteFornecedorId": 1},
                        {"sequencialResultado": 2, "porteFornecedorId": 5,
                         "situacaoCompraItemResultadoId": 2}]
        purchase = {"numeroControlePNCP": "id", "orgaoEntidade": {"cnpj": "123"},
                    "anoCompra": 2025, "sequencialCompra": 1, "modalidadeId": 6,
                    "dataPublicacaoPncp": "2025-01-01",
                    "unidadeOrgao": {"codigoIbge": "2304400", "municipioNome": "Fortaleza"}}
        rows, errors, audit = collect_purchase(FakeClient(), purchase)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["porte"], "ME")
        self.assertEqual(errors, [])
        self.assertEqual(audit["duplicatas"], 1)
        self.assertEqual(audit["resultados_cancelados"], 1)
        self.assertEqual(audit["itens_sem_resultado"], 1)

    def test_falha_http_nao_gera_registro_ausente(self):
        from scripts.spike_pncp_porte import collect_purchase
        class FakeClient:
            def pages(self, url):
                raise RuntimeError("HTTP 404")
        purchase = {"numeroControlePNCP": "id", "orgaoEntidade": {"cnpj": "123"},
                    "anoCompra": 2025, "sequencialCompra": 1}
        rows, errors, audit = collect_purchase(FakeClient(), purchase)
        self.assertEqual(rows, [])
        self.assertEqual(errors[0]["etapa"], "itens")


if __name__ == "__main__":
    unittest.main()

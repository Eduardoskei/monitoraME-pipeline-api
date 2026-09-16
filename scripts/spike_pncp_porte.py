"""Spike reproduzivel: python -m scripts.spike_pncp_porte --output docs/spikes/pncp-porte.

Amostra em dois estagios (compras e itens), estratificada por municipio/mes/modalidade.
Somente leitura nas APIs publicas; independente de banco; URLs configuradas pelo .env.
"""
import argparse
import calendar
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import time
import threading

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

CATEGORIES = ("MEI", "ME", "EPP", "Demais Empresas", "Não se aplica", "Não informado")
PORTES = {1: "ME", 2: "EPP", 3: "Demais Empresas", 4: "Não se aplica", 5: "Não informado", 6: "MEI"}
MUNICIPIOS = {"2304400": "Fortaleza", "2300754": "Amontada", "2312908": "Sobral", "2307304": "Juazeiro do Norte"}
MONTHS = ("2025-01", "2025-05", "2025-09")
MODALIDADES = (6, 8, 9)
def pncp_base(env_name, default):
    value = os.getenv(env_name, default).strip().rstrip("/")
    if not value:
        raise ValueError(f"{env_name} nao pode ser vazia")
    return value if value.endswith("/v1") else value + "/v1"


CONSULTA = pncp_base("PNCP_CONSULTA_BASE_URL", "https://pncp.gov.br/api/consulta")
GESTAO = pncp_base("PNCP_GESTAO_BASE_URL", "https://pncp.gov.br/api/pncp")


def classify(result):
    value = result.get("porteFornecedorId")
    if value is None or (isinstance(value, str) and not value.strip()):
        return "Não informado", "ausente_nulo_vazio"
    # Do not coerce booleans/fractions into valid catalog codes.
    key = str(value).strip()
    if key not in {str(k) for k in PORTES}:
        return "Não informado", "codigo_invalido"
    code = int(key)
    return PORTES[code], "codigo_5" if code == 5 else "valido"


def distribution(rows):
    counts = Counter(row["porte"] for row in rows)
    return [{"porte": cat, "quantidade": counts[cat],
             "percentual": round(100 * counts[cat] / len(rows), 4) if rows else None}
            for cat in CATEGORIES]


def aggregate(rows, fields, keys=()):
    groups = defaultdict(list)
    for key in keys:
        groups[key] = []
    for row in rows:
        groups[tuple(row.get(field, "") for field in fields)].append(row)
    return [{**dict(zip(fields, key)), "total": len(group), **entry}
            for key, group in sorted(groups.items()) for entry in distribution(group)]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class Client:
    def __init__(self, output, *, max_attempts=5, timeout=(10, 40)):
        if max_attempts < 1:
            raise ValueError("max_attempts deve ser positivo")
        self.max_attempts = max_attempts
        self.timeout = timeout
        self.cancelled = False
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.cache = output / "raw"
        self.cache.mkdir(parents=True, exist_ok=True)

    def get(self, url, params=None):
        if self.cancelled:
            raise RuntimeError("Coleta cancelada apos falha de outra consulta")
        prepared = requests.Request("GET", url, params=params).prepare().url
        path = self.cache / (hashlib.sha256(prepared.encode()).hexdigest() + ".json")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["payload"]
        for attempt in range(self.max_attempts):
            if self.cancelled:
                raise RuntimeError("Coleta cancelada apos falha de outra consulta")
            try:
                with self.lock:
                    time.sleep(max(0, self.next_request - time.monotonic()))
                    self.next_request = time.monotonic() + 1.5
                response = requests.get(prepared, timeout=self.timeout)
                if response.status_code == 429:
                    with self.lock:
                        self.next_request = max(self.next_request, time.monotonic() + 30 * (attempt + 1))
                # 404 is a collection error, never a missing porte or an empty result.
                response.raise_for_status()
                payload = [] if response.status_code == 204 else response.json()
                write_json(path, {"url": prepared, "collected_at": datetime.now(timezone.utc).isoformat(),
                                  "status": response.status_code, "payload": payload})
                return payload
            except (requests.RequestException, ValueError):
                if attempt == self.max_attempts - 1:
                    raise
                time.sleep(2 ** attempt)

    def pages(self, url, params=None):
        rows, page = [], 1
        expected_pages = None
        while True:
            payload = self.get(url, {**(params or {}), "pagina": page, "tamanhoPagina": 50})
            batch = payload if isinstance(payload, list) else payload.get("data")
            if not isinstance(batch, list):
                raise ValueError("Resposta de pagina sem lista de dados")
            rows.extend(batch)
            total = payload.get("totalPaginas") if isinstance(payload, dict) else None
            if total is not None:
                expected_pages = int(total)
            if not batch and expected_pages is not None and page <= expected_pages and (page > 1 or expected_pages > 1):
                raise ValueError(f"Pagina {page} vazia antes de completar {expected_pages} paginas")
            if not batch or (expected_pages is not None and page >= expected_pages) or (expected_pages is None and len(batch) < 50):
                return rows
            page += 1


def collect_frame(client, cell, per_cell, seed):
    city, month, modality = cell
    year, month_number = map(int, month.split("-"))
    params = {"dataInicial": f"{year}{month_number:02d}01",
              "dataFinal": f"{year}{month_number:02d}{calendar.monthrange(year, month_number)[1]}",
              "codigoModalidadeContratacao": modality, "uf": "CE", "codigoMunicipioIbge": city}
    purchases = client.pages(CONSULTA + "/contratacoes/publicacao", params)
    unique = {p["numeroControlePNCP"]: p for p in purchases}
    ordered = [unique[k] for k in sorted(unique)]
    selected = random.Random(f"{seed}:{cell}").sample(ordered, min(per_cell, len(ordered)))
    audit = {"municipio_ibge": city, "municipio": MUNICIPIOS[city], "periodo": month,
             "modalidade": modality, "compras_universo": len(ordered),
             "compras_sorteadas": len(selected), "fracao_amostral_compras": len(selected) / len(ordered) if ordered else None, "status": "ok",
             "selecionadas": [p["numeroControlePNCP"] for p in selected]}
    return audit, selected


def collect_purchase(client, purchase, max_items=5, seed=20260914):
    rows, errors = [], []
    purchase_id = purchase["numeroControlePNCP"]
    base = (f"{GESTAO}/orgaos/{purchase['orgaoEntidade']['cnpj']}/compras/"
            f"{purchase['anoCompra']}/{purchase['sequencialCompra']}")
    audit = {"compra": purchase_id, "itens": 0, "itens_sem_resultado": 0,
             "resultados_cancelados": 0, "duplicatas": 0}
    try:
        items = client.pages(base + "/itens")
    except Exception as error:
        return rows, [{ "compra": purchase_id, "etapa": "itens", "erro": str(error)}], audit
    audit["itens_universo"] = len(items)
    items = random.Random(f"{seed}:{purchase_id}:itens").sample(
        sorted(items, key=lambda item: item["numeroItem"]), min(max_items, len(items)))
    audit["itens_sorteados"] = [item["numeroItem"] for item in items]
    seen = set()
    for item in items:
        number = item["numeroItem"]
        audit["itens"] += 1
        try:
            results = client.get(f"{base}/itens/{number}/resultados")
            if not isinstance(results, list):
                raise ValueError("Resposta de resultados nao e lista")
            if not results:
                audit["itens_sem_resultado"] += 1
            for result in results:
                if result.get("dataCancelamento") or result.get("situacaoCompraItemResultadoId") == 2:
                    audit["resultados_cancelados"] += 1
                    continue
                sequence = result.get("sequencialResultado")
                if sequence is None:
                    raise ValueError("Resultado sem sequencialResultado")
                key = (number, sequence)
                if key in seen:
                    audit["duplicatas"] += 1
                    continue
                seen.add(key)
                porte, reason = classify(result)
                rows.append({"compra": purchase_id, "item": number, "resultado": sequence,
                             "municipio_ibge": purchase["unidadeOrgao"]["codigoIbge"],
                             "municipio": purchase["unidadeOrgao"]["municipioNome"],
                             "periodo": purchase["dataPublicacaoPncp"][:7],
                             "orgao": purchase["orgaoEntidade"]["cnpj"],
                             "modalidade": purchase["modalidadeId"],
                             "fornecedor": result.get("niFornecedor") or "",
                             "tipo_pessoa": result.get("tipoPessoa") or "",
                             "data_resultado": result.get("dataResultado"),
                             "porte_id_original": result.get("porteFornecedorId"),
                             "porte_nome_original": result.get("porteFornecedorNome"),
                             "porte": porte, "motivo": reason})
        except Exception as error:
            errors.append({"compra": purchase_id, "item": number, "etapa": "resultados", "erro": str(error)})
    return rows, errors, audit


def report(output, rows, manifest):
    overall = distribution(rows)
    write_csv(output / "resultados.csv", rows)
    write_csv(output / "distribuicao.csv", overall)
    planned_keys = {
        "municipio": [(city, name) for city, name in MUNICIPIOS.items()],
        "periodo": [(month,) for month in MONTHS],
        "municipio_periodo": [(city, name, month) for city, name in MUNICIPIOS.items() for month in MONTHS],
        "modalidade": [(mod,) for mod in MODALIDADES],
    }
    for name, fields in {"municipio": ["municipio_ibge", "municipio"], "periodo": ["periodo"],
                         "municipio_periodo": ["municipio_ibge", "municipio", "periodo"],
                         "orgao": ["orgao"], "modalidade": ["modalidade"],
                         "fornecedor": ["fornecedor"], "tipo_pessoa": ["tipo_pessoa"]}.items():
        write_csv(output / f"por_{name}.csv", aggregate(rows, fields, planned_keys.get(name, ())))
    suppliers = defaultdict(set)
    for row in rows:
        if row["fornecedor"]:
            suppliers[row["fornecedor"]].add(row["porte"])
    reasons = Counter(row["motivo"] for row in rows)
    pf_business = sum(row["tipo_pessoa"] == "PF" and row["porte"] in CATEGORIES[:4] for row in rows)
    purchases_with_results = len({row["compra"] for row in rows})
    manifest["qualidade"] = {"motivos": dict(reasons),
        "fornecedores_distintos": len(suppliers), "pessoas_fisicas_com_porte_empresarial": pf_business,
        "fornecedores_com_multiplos_portes": sum(len(ports) > 1 for ports in suppliers.values()),
        "registros_sem_identificador_fornecedor": sum(not r["fornecedor"] for r in rows)}
    write_json(output / "manifesto.json", manifest)
    lines = ["# Spike: cobertura de porte no PNCP", "",
             f"Execução UTC: {manifest['executado_em']}. Semente: {manifest['seed']}.", "",
             "## Método e população", "",
             "Recorte exploratório intencional: quatro municípios do Ceará, janeiro, maio e setembro de 2025; "
             "pregão eletrônico (6), dispensa (8) e inexigibilidade (9). Listagem integral de cada estrato "
             "município × mês × modalidade, seguida de sorteio uniforme sem reposição de até "
             f"{manifest['por_estrato']} compras por estrato. Até {manifest['max_itens']} itens são sorteados uniformemente por compra, e todos os seus resultados "
             "são consultados. A unidade é resultado de item/fornecedor, não compra nem fornecedor distinto. "
             "Município é o da unidade compradora; período é o mês de publicação da compra, não de homologação.",
             "", "A distribuição abaixo é descritiva da amostra, sem ponderação. Compras com mais itens sorteados ou mais resultados por item "
             "pesam mais. Municípios e meses não foram sorteados e os estratos têm frações amostrais diferentes; "
             "não extrapolar percentuais para Ceará/Brasil, nem usar intervalo binomial como se os registros "
             "fossem independentes. A diversidade de órgãos e fornecedores é observada, não uma cota garantida.",
             "", "## Distribuição", "", "| Porte | Registros | Percentual |", "|---|---:|---:|"]
    for entry in overall:
        percent = f"{entry['percentual']:.2f}%" if entry["percentual"] is not None else "N/D"
        lines.append(f"| {entry['porte']} | {entry['quantidade']} | {percent} |")
    lines += ["", f"Total: {len(rows)} resultados ativos; {len(suppliers)} fornecedores identificados; "
              f"{len({r['orgao'] for r in rows})} órgãos; {len({r['compra'] for r in rows})} compras com resultados.",
              "", "## Comparação por município e período", "",
              "| Município | Período | Total | Não informado | % |", "|---|---|---:|---:|---:|"]
    for entry in aggregate(rows, ["municipio", "periodo"], [(name, month) for name in MUNICIPIOS.values() for month in MONTHS]):
        if entry["porte"] == "Não informado":
            pct = f"{entry['percentual']:.2f}%" if entry['percentual'] is not None else "N/D"
            lines.append(f"| {entry['municipio']} | {entry['periodo']} | {entry['total']} | "
                         f"{entry['quantidade']} | {pct} |")
    lines += ["", "As distribuições completas das seis categorias por município, período, município/período, "
              "órgão, modalidade e fornecedor estão nos CSVs adjacentes. Estratos vazios/falhos e compras "
              "sem resultados constam no manifesto, nunca como porte ausente. N/D indica denominador zero, não cobertura de 100%.", "",
              "## Qualidade e conclusão", "",
              f"Das {len(manifest['compras'])} compras sorteadas, {purchases_with_results} têm resultados "
              f"elegíveis nos itens amostrados e {len(manifest['compras']) - purchases_with_results} não têm. "
              f"Foram consultados {sum(a['itens'] for a in manifest['compras'])} itens sorteados; "
              f"{sum(a['itens_sem_resultado'] for a in manifest['compras'])} retornaram lista de resultados vazia. "
              "Isso limita a observabilidade de fornecedores e é distinto da ausência do campo porte.",
              "", f"Alerta semântico: {pf_business} resultados identificam o fornecedor como pessoa física (PF), "
              "mas atribuem MEI, ME, EPP ou Demais Empresas. O catálogo prevê Não se aplica para situações "
              "como pessoas físicas. Revisar a consistência da declaração sem reclassificar automaticamente; "
              "os valores originais permanecem preservados e o recorte por tipo de pessoa está no CSV.",
              "",
              f"Motivos de classificação: {json.dumps(dict(reasons), ensure_ascii=False)}. "
              "'Não informado' agrega código 5, campo ausente/nulo/vazio e código inválido; "
              "os motivos permanecem separados em resultados.csv e no manifesto. 'Não se aplica' "
              "permanece categoria própria e integra o denominador total.",
              "", f"Falhas de coleta: {len(manifest['erros'])}; resultados cancelados excluídos: "
              f"{sum(a['resultados_cancelados'] for a in manifest['compras'])}. "
              "Falhas não entram no denominador. Consultas e respostas bem-sucedidas estão em raw/, "
              "com URL e horário. Nova execução no mesmo diretório reutiliza o cache; use outro diretório "
              "para uma nova fotografia. Os números refletem os dados disponíveis na coleta, sujeitos a retificação.",
              "", f"Fornecedores com mais de uma categoria: {manifest['qualidade']['fornecedores_com_multiplos_portes']}. "
              "Essa divergência pode decorrer de datas distintas ou declaração inconsistente e não demonstra, "
              "sozinha, erro cadastral. Cobertura não comprova exatidão: não foi feita validação contra a base CNPJ."]
    if rows:
        missing = next(e for e in overall if e["porte"] == "Não informado")
        lines += ["", f"Na amostra, **{missing['percentual']:.2f}% ({missing['quantidade']}/{len(rows)})** "
                  "dos resultados não possuem porte utilizável. O campo pode integrar o processo como "
                  "informação complementar, preservando fonte e data. O enriquecimento pela base CNPJ "
                  "continua necessário para validação e preenchimento; a amostra não autoriza sua substituição."]
    else:
        lines += ["", "Sem resultados elegíveis: cobertura indeterminada; nenhuma conclusão quantitativa."]
    if manifest["erros"]:
        lines += ["", "**Coleta incompleta:** as falhas podem introduzir viés adicional; resolver e repetir "
                  "antes de usar estes percentuais como evidência definitiva."]
    lines += ["", "O catálogo consultado informa inclusão do código 6 (MEI) em 2026-06-11. "
              "Zero MEI neste recorte de 2025 não significa inexistência de MEIs; não inferir MEI a partir de ME. "
              "Para avaliar adoção do novo código, repetir com períodos posteriores à inclusão.",
              "", "Próximo passo para decisão abrangente: ampliar municípios e meses, planejar precisão "
              "considerando conglomerados e pesos e confrontar CNPJs com referência temporal compatível. "
              "Não há limiar de aprovação de cobertura definido pelo produto.", "",
              "## Fontes", "",
              "- [Catálogo oficial consultado](https://pncp.gov.br/api/pncp/v1/portes-empresa).",
              "- [Consulta de porte no manual PNCP](https://pncp.gov.br/manual/pt-br/latest/tabelas_de_dominio/consultar_porte_de_empresa.html).",
              "- [Resultado de item no manual PNCP](https://pncp.gov.br/manual/pt-br/latest/contratacao/inserir_resultado_do_item_de_uma_contratacao.html).",
              "", "## Reprodução", "",
              "```powershell", ".venv/Scripts/python.exe scripts/spike_pncp_porte.py --output docs/spikes/pncp-porte",
              ".venv/Scripts/python.exe -m unittest discover -s tests -p test_spike_pncp_porte.py", "```", ""]
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--por-estrato", type=int, default=2)
    parser.add_argument("--max-itens", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260914)
    args = parser.parse_args()
    if args.por_estrato < 1 or args.max_itens < 1:
        parser.error("--por-estrato e --max-itens devem ser positivos")
    args.output.mkdir(parents=True, exist_ok=True)
    client = Client(args.output)
    catalog = client.get(GESTAO + "/portes-empresa")
    write_json(args.output / "catalogo_portes.json", catalog)
    manifest = {"executado_em": datetime.now(timezone.utc).isoformat(), "seed": args.seed,
                "por_estrato": args.por_estrato, "max_itens": args.max_itens, "estratos": [], "compras": [], "erros": []}
    selected, rows = [], []
    cells = [(city, month, mod) for city in MUNICIPIOS for month in MONTHS for mod in MODALIDADES]
    def frame(cell):
        try:
            return collect_frame(client, cell, args.por_estrato, args.seed)
        except Exception as error:
            return {"municipio_ibge": cell[0], "periodo": cell[1], "modalidade": cell[2],
                    "status": "falha", "erro": str(error)}, []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for audit, purchases in pool.map(frame, cells):
            manifest["estratos"].append(audit)
            selected.extend(purchases)
            if audit["status"] != "ok":
                manifest["erros"].append(audit)
    write_json(args.output / "compras_sorteadas.json", selected)
    print(f"Estratos: {len(cells)}; compras sorteadas: {len(selected)}", flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for index, (batch, errors, audit) in enumerate(pool.map(lambda p: collect_purchase(client, p, args.max_itens, args.seed), selected), 1):
            rows.extend(batch)
            manifest["erros"].extend(errors)
            manifest["compras"].append(audit)
            print(f"Compra {index}/{len(selected)}; resultados: {len(rows)}; falhas: {len(manifest['erros'])}", flush=True)
    report(args.output, rows, manifest)


if __name__ == "__main__":
    main()

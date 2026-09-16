"""PEN-005: amostra probabilistica de publicacoes PNCP no Ceara, ano 2025.

Executar: python -m scripts.spike_pncp_representatividade --output docs/spikes/pncp-ce-2025
Nao requer banco. Reutiliza apenas respostas armazenadas no diretorio desta execucao.
"""
import argparse
import calendar
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import math
from pathlib import Path
import random
import statistics

from scripts.spike_pncp_porte import Client as BaseClient, CATEGORIES, CONSULTA, GESTAO, classify, write_csv, write_json

Z95 = statistics.NormalDist().inv_cdf(0.975)
YEAR = 2025
UF = "CE"
SEED = 20260915



class Client(BaseClient):
    """Falhas persistentes interrompem a rodada sem horas de retentativas."""
    def __init__(self, output):
        super().__init__(output, max_attempts=2, timeout=(10, 60))


def parallel_results(function, items, client, workers=4):
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = [pool.submit(function, item) for item in items]
    try:
        for future in as_completed(futures):
            yield future.result()
    except BaseException:
        client.cancelled = True
        for future in futures:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def incomplete_report(output, manifest):
    plan = manifest.get("planejamento", {})
    lines = [
        "# PEN-005 — Ceará, ano completo de 2025", "",
        "**Status: coleta incompleta. PEN-005 ainda não concluído.**", "",
        "População confirmada: contratações publicadas no PNCP em 2025, com unidade "
        "compradora no Ceará, abrangendo todos os municípios e modalidades disponíveis.", "",
        "## Implementado e validado", "",
        f"- Cadastro: {len(manifest['estratos'])} estratos de trimestre × modalidade.",
        f"- Universo identificado: {plan.get('populacao_compras', 'ainda não determinado')} contratações.",
        f"- Dimensionamento: {plan.get('n_planejado', 'ainda não determinado')} compras; referência "
        "AAS de 95% de confiança, p=0,5 e ±5 p.p., com correção para população finita.",
        "- Sorteio sem reposição por posições em todas as páginas, com semente e probabilidades "
        "preservadas em plano_amostral.csv. Não há seleção intencional de municípios.",
        "- Até dois itens sorteados por compra; estimativa ponderada por probabilidade de inclusão.",
        "- Variância e IC95% por linearização, considerando os dois estágios e população finita.",
        "- Teste matemático por enumeração exata dos 27 sorteios de uma população de teste; "
        "a referência de ±5 p.p. não é uma precisão já alcançada pelos resultados.", "",
        "## Impedimento observado", "",
        f"Etapa interrompida: {manifest.get('etapa', 'cadastro')}.",
    ]
    for error in manifest["erros"]:
        lines.append(f"- {error['tipo']}: {error['mensagem']}")
    diagnostic = output / "diagnostico_api.json"
    if diagnostic.exists():
        d = json.loads(diagnostic.read_text(encoding="utf-8-sig"))
        lines += ["", f"Diagnóstico adicional da API: HTTP {d['status_http']} — {d['mensagem']}. "
                  "Evidência registrada em diagnostico_api.json."]
    lines += ["", "Não foram produzidos percentuais populacionais nem intervalos de confiança reais "
              "para esta rodada incompleta. Os 7,07% do piloto de 99 resultados continuam sendo apenas "
              "descritivos daquele recorte e não representam Ceará/2025.", "",
              "## Para concluir", "",
              "Executar novamente quando a consulta de contratações do PNCP responder. A rotina "
              "reaproveita o cache, conclui as páginas sorteadas e os resultados dos itens, revalida "
              "o cadastro e somente então gera as estimativas e marca a execução como concluída.",
              "", "```powershell",
              "python -m scripts.spike_pncp_representatividade --output docs/spikes/pncp-ce-2025",
              "python -m unittest discover -s tests", "```", "",
              "O requisito de consulta sobre amostra representativa depende da conclusão da coleta. "
              "O plano estatístico implementado, isoladamente, não satisfaz esse requisito.", ""]
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")

def allocation(sizes, reference_error=0.05):
    if not 0 < reference_error < 1:
        raise ValueError("Margem de referencia deve estar entre zero e um")
    total = sum(sizes.values())
    n0 = math.ceil(Z95 ** 2 * 0.25 / reference_error ** 2)
    target = math.ceil(total * n0 / (total + n0 - 1)) if total else 0
    target = max(target, sum(min(2, size) for size in sizes.values()))
    quotas = {key: target * size / total if total else 0 for key, size in sizes.items()}
    counts = {key: min(size, max(2, math.floor(quotas[key]))) for key, size in sizes.items()}
    while sum(counts.values()) < target:
        key = max((k for k in sizes if counts[k] < sizes[k]), key=lambda k: (quotas[k] - counts[k], k))
        counts[key] += 1
    while sum(counts.values()) > target:
        key = max((k for k in sizes if counts[k] > min(2, sizes[k])), key=lambda k: (counts[k] - quotas[k], k))
        counts[key] -= 1
    return counts, {"n0_aas": n0, "n_planejado": target, "margem_referencia_aas": reference_error,
                    "confianca": 0.95, "populacao_compras": total}


def frame_params(quarter, modality):
    start = 3 * quarter - 2
    end = start + 2
    return {"dataInicial": f"{YEAR}{start:02d}01",
            "dataFinal": f"{YEAR}{end:02d}{calendar.monthrange(YEAR, end)[1]}",
            "codigoModalidadeContratacao": modality, "uf": UF}


def checked_page(client, params, page, expected_total=None):
    payload = client.get(CONSULTA + "/contratacoes/publicacao",
                         {**params, "pagina": page, "tamanhoPagina": 50})
    if payload == []:
        if page != 1 or expected_total not in (None, 0):
            raise ValueError("Pagina vazia em cadastro nao vazio")
        return 0, []
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Resposta de publicacoes invalida")
    total = payload.get("totalRegistros")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("totalRegistros ausente/invalido")
    if expected_total is not None and total != expected_total:
        raise ValueError("Total do cadastro mudou entre consultas")
    if payload.get("numeroPagina") != page or payload.get("totalPaginas") != math.ceil(total / 50):
        raise ValueError("Metadados de paginacao inconsistentes")
    batch = payload["data"]
    if len(batch) != min(50, max(0, total - (page - 1) * 50)):
        raise ValueError("Pagina incompleta")
    return total, batch


def select_purchases(client, cell, n, seed):
    total = cell["N"]
    positions = sorted(random.Random(f"{seed}:{cell['id']}:compras").sample(range(total), n))
    selected, pages = [], {}
    for position in positions:
        page = position // 50 + 1
        if page not in pages:
            _, pages[page] = checked_page(client, cell["params"], page, total)
        purchase = pages[page][position % 50]
        date = purchase["dataPublicacaoPncp"][:10].replace("-", "")
        if (purchase["unidadeOrgao"]["ufSigla"] != UF or
            str(purchase["modalidadeId"]) != str(cell["modalidade"]) or
            not cell["params"]["dataInicial"] <= date <= cell["params"]["dataFinal"]):
            raise ValueError("Publicacao fora do estrato sorteado")
        selected.append({"estrato": cell["id"], "posicao_zero_based": position,
                         "pagina": page, "pi_compra": n / total, "publicacao": purchase})
    if len({row["publicacao"]["numeroControlePNCP"] for row in selected}) != n:
        raise ValueError("Compras duplicadas nas posicoes sorteadas")
    return selected


def all_items(client, url):
    items, seen, page = [], set(), 1
    while True:
        batch = client.get(url, {"pagina": page, "tamanhoPagina": 500})
        if not isinstance(batch, list):
            raise ValueError("Resposta de itens invalida")
        for item in batch:
            number = item.get("numeroItem")
            if number is None or number in seen:
                raise ValueError("Item sem identificacao ou duplicado na paginacao")
            seen.add(number)
            items.append(item)
        if len(batch) < 500:
            return items
        page += 1


def collect(client, selected, max_items, seed):
    purchase = selected["publicacao"]
    purchase_id = purchase["numeroControlePNCP"]
    base = (f"{GESTAO}/orgaos/{purchase['orgaoEntidade']['cnpj']}/compras/"
            f"{purchase['anoCompra']}/{purchase['sequencialCompra']}")
    meta = {"compra": purchase_id, "estrato": selected["estrato"],
            "municipio_ibge": str(purchase["unidadeOrgao"]["codigoIbge"]),
            "municipio": purchase["unidadeOrgao"]["municipioNome"],
            "periodo": purchase["dataPublicacaoPncp"][:7],
            "orgao": purchase["orgaoEntidade"]["cnpj"], "modalidade": purchase["modalidadeId"],
            "pi_compra": selected["pi_compra"]}
    items = all_items(client, base + "/itens")
    M = len(items)
    m = min(max_items, M)
    chosen = random.Random(f"{seed}:{purchase_id}:itens").sample(sorted(items, key=lambda r: r["numeroItem"]), m)
    observations, rows, cancelled = [], [], 0
    for item in chosen:
        number = item["numeroItem"]
        results = client.get(f"{base}/itens/{number}/resultados")
        if not isinstance(results, list):
            raise ValueError("Resposta de resultados invalida")
        counts = Counter()
        seen = set()
        for result in results:
            sequence = result.get("sequencialResultado")
            if sequence is None or sequence in seen:
                raise ValueError("Resultado sem identificacao ou duplicado")
            seen.add(sequence)
            if result.get("dataCancelamento") or result.get("situacaoCompraItemResultadoId") == 2:
                cancelled += 1
                continue
            porte, reason = classify(result)
            counts[porte] += 1
            pi = selected["pi_compra"] * m / M
            rows.append({**meta, "item": number, "resultado": sequence, "M": M, "m": m,
                         "pi_item_dada_compra": m / M, "pi_resultado": pi, "peso": 1 / pi,
                         "porte": porte, "motivo": reason, "porte_id_original": result.get("porteFornecedorId"),
                         "porte_nome_original": result.get("porteFornecedorNome"),
                         "fornecedor": result.get("niFornecedor") or "", "tipo_pessoa": result.get("tipoPessoa") or ""})
        observations.append({"item": number, "total": sum(counts.values()),
                             "contagens": {category: counts[category] for category in CATEGORIES}})
    return {**meta, "M": M, "m": m, "observacoes": observations,
            "resultados_cancelados": cancelled, "resultados": rows}


def estimate(units, cells, category, domain=None):
    """Razao de totais HT. Variancia por linearizacao com os dois estagios SRS."""
    by_cell = defaultdict(list)
    X, Y = 0.0, 0.0
    domain_units = 0
    raw_total = raw_category = 0
    for unit in units:
        keep = domain is None or all(str(unit[key]) == str(value) for key, value in domain.items())
        x = [o["total"] if keep else 0 for o in unit["observacoes"]]
        y = [o["contagens"][category] if keep else 0 for o in unit["observacoes"]]
        factor = unit["M"] / unit["m"] if unit["m"] else 0
        xt, yt = factor * sum(x), factor * sum(y)
        X += xt / unit["pi_compra"]
        Y += yt / unit["pi_compra"]
        raw_total += sum(x)
        raw_category += sum(y)
        domain_units += int(sum(x) > 0)
        by_cell[unit["estrato"]].append((unit, x, y, xt, yt))
    base = {"porte": category, "quantidade_amostral": raw_category, "total_amostral": raw_total,
            "compras_com_resultados": domain_units, "total_estimado_resultados": X,
            "total_estimado_categoria": Y}
    if not X:
        return {**base, "percentual": None, "erro_padrao_pp": None, "margem_95_pp": None,
                "ic95_inferior": None, "ic95_superior": None, "status_ic": "sem_resultados"}
    ratio = Y / X
    variance = 0.0
    for cell in cells:
        N, n = cell["N"], cell["n"]
        if not N:
            continue
        sample = by_cell[cell["id"]]
        if len(sample) != n or (n < 2 and n != N):
            raise ValueError("Amostra incompleta ou estrato sem replicacao para variancia")
        residual_totals, within = [], 0.0
        for unit, x, y, xt, yt in sample:
            M, m = unit["M"], unit["m"]
            residual_totals.append(yt - ratio * xt)
            if M > m:
                if m < 2:
                    raise ValueError("Segundo estagio sem replicacao para variancia")
                residuals = [yi - ratio * xi for xi, yi in zip(x, y)]
                within += M ** 2 * (1 - m / M) * statistics.variance(residuals) / m
        if n < N:
            variance += N ** 2 * (1 - n / N) * statistics.variance(residual_totals) / n
        variance += N / n * within
    se = math.sqrt(max(0, variance)) / X
    census = all(cell["n"] == cell["N"] for cell in cells) and all(u["m"] == u["M"] for u in units)
    estimable = census or (raw_category > 0 and raw_category < raw_total)
    return {**base, "percentual": 100 * ratio, "erro_padrao_pp": 100 * se,
            "margem_95_pp": 100 * Z95 * se if estimable else None,
            "ic95_inferior": 100 * max(0, ratio - Z95 * se) if estimable else None,
            "ic95_superior": 100 * min(1, ratio + Z95 * se) if estimable else None,
            "status_ic": "censo" if census else "aproximado_linearizacao" if estimable else "sem_eventos_para_estimar"}


def summarize(output, units, manifest):
    cells = manifest["estratos"]
    rows = [row for unit in units for row in unit["resultados"]]
    estimates = [estimate(units, cells, category) for category in CATEGORIES]
    write_csv(output / "resultados.csv", rows)
    write_csv(output / "estimativas_ponderadas.csv", estimates)
    counts = Counter(row["porte"] for row in rows)
    write_csv(output / "distribuicao_amostral.csv", [
        {"porte": c, "quantidade": counts[c], "percentual": 100 * counts[c] / len(rows) if rows else None}
        for c in CATEGORIES])
    for field in ("municipio_ibge", "periodo", "orgao", "modalidade"):
        values = sorted({str(unit[field]) for unit in units})
        if field == "periodo":
            values = [f"{YEAR}-{month:02d}" for month in range(1, 13)]
        grouped = [{field: value, **estimate(units, cells, c, {field: value})}
                   for value in values for c in CATEGORIES]
        write_csv(output / f"por_{field}.csv", grouped)
    city_months = sorted({(unit["municipio_ibge"], unit["periodo"]) for unit in units})
    write_csv(output / "por_municipio_periodo.csv", [
        {"municipio_ibge": city, "periodo": month,
         **estimate(units, cells, c, {"municipio_ibge": city, "periodo": month})}
        for city, month in city_months for c in CATEGORIES])
    supplier_groups = defaultdict(list)
    for row in rows:
        supplier_groups[row["fornecedor"]].append(row)
    write_csv(output / "por_fornecedor.csv", [
        {"fornecedor": supplier, "porte": c, "quantidade_amostral": sum(r["porte"] == c for r in group),
         "total_amostral": len(group),
         "percentual_amostral": 100 * sum(r["porte"] == c for r in group) / len(group)}
        for supplier, group in sorted(supplier_groups.items()) for c in CATEGORIES])
    write_csv(output / "municipios_observados.csv", [
        {"municipio_ibge": city, "municipio": next(u["municipio"] for u in units if u["municipio_ibge"] == city),
         "compras_sorteadas": sum(u["municipio_ibge"] == city for u in units),
         "resultados_observados": sum(r["municipio_ibge"] == city for r in rows)}
        for city in sorted({u["municipio_ibge"] for u in units})])
    missing = estimates[-1]
    weights = [row["peso"] for row in rows]
    manifest["resumo"] = {
        "resultados_ativos": len(rows), "itens_sorteados": sum(u["m"] for u in units),
        "itens_sem_resultado_ativo": sum(o["total"] == 0 for u in units for o in u["observacoes"]),
        "compras_sem_resultado_ativo": sum(not u["resultados"] for u in units),
        "municipios_sorteados": len({u["municipio_ibge"] for u in units}),
        "municipios_com_resultados": len({r["municipio_ibge"] for r in rows}),
        "orgaos_com_resultados": len({r["orgao"] for r in rows}),
        "fornecedores_identificados": len({r["fornecedor"] for r in rows if r["fornecedor"]}),
        "periodos_sorteados": sorted({u["periodo"] for u in units}),
        "modalidades_sorteadas": sorted({u["modalidade"] for u in units}),
        "cancelados_excluidos": sum(u["resultados_cancelados"] for u in units),
        "motivos": dict(Counter(r["motivo"] for r in rows)),
        "pf_com_porte_empresarial": sum(r["tipo_pessoa"] == "PF" and r["porte"] in CATEGORIES[:4] for r in rows),
        "n_efetivo_kish_diagnostico": sum(weights) ** 2 / sum(w ** 2 for w in weights) if weights else 0,
        "nao_informado": missing,
    }
    # PEN-005 means probabilistic coverage and a completed, auditable collection.
    # It does not assert a precision threshold that the product did not define.
    manifest["status"] = "concluido" if rows and not manifest["erros"] else "inconclusivo"
    write_json(output / "manifesto.json", manifest)
    write_json(output / "unidades_amostrais.json", units)
    s = manifest["resumo"]
    fmt = lambda value: "N/D" if value is None else f"{value:.2f}"
    lines = ["# PEN-005 — Ceará, ano completo de 2025", "",
        f"Status: **{manifest['status']}**. Execução UTC: {manifest['executado_em']}.",
        "", "## População e desenho",
        "", "População-alvo confirmada: contratações publicadas no PNCP de 01/01/2025 a 31/12/2025 "
        "cuja unidade compradora está no Ceará, incluindo órgãos municipais, estaduais e federais. "
        "Todos os municípios têm acesso ao sorteio, sem seleção intencional de cidades. "
        "Todas as modalidades do catálogo oficial foram consultadas em cada trimestre, inclusive inativas; "
        "combinações sem publicações foram registradas como estratos vazios.",
        "", f"O cadastro consultável contém **{manifest['planejamento']['populacao_compras']} publicações**, "
        f"distribuídas em {len(cells)} estratos trimestre × modalidade. Foram sorteadas "
        f"**{len(units)} compras**, com semente {manifest['seed']}, sem reposição. "
        f"Em cada compra, até {manifest['max_itens']} itens foram sorteados sem reposição; "
        "todos os resultados desses itens foram consultados. Compras/itens sem resultados têm contribuição zero "
        "para os totais de fornecedores, mas permanecem no desenho e na variância.",
        "", "O cadastro é acessado por posições: totalRegistros define N; índices uniformes entre 0 e N−1 "
        "definem página e posição. Assim, publicações de páginas intermediárias/finais têm a mesma chance "
        "que as da primeira página. O total, tamanho e número de cada página acessada são validados. "
        "As primeiras páginas são consultadas novamente ao fim para detectar mudança do cadastro. "
        "O PNCP não oferece uma fotografia transacional: a inferência pressupõe ordenação estável "
        "durante a janela registrada nas respostas, e não cobre publicações ausentes da fonte.",
        "", "## Dimensionamento e inferência",
        "", f"Referência de planejamento: AAS, confiança de 95%, p=0,5 e ±5 pontos percentuais: "
        f"n0=ceil(z²×0,25/0,05²)={manifest['planejamento']['n0_aas']}. Aplicou-se a correção "
        "para população finita e a alocação proporcional, com ao menos duas compras por estrato "
        "não censitário. Estratos de uma compra são censitários. Essa referência dimensiona compras; "
        "**não garante ±5 p.p. para resultados de fornecedores**, pois há dois estágios e pesos distintos.",
        "", "Probabilidade por compra: pi1=n_h/N_h. Por item: pi2=m_i/M_i. "
        "Peso por resultado: w=1/(pi1×pi2). O percentual populacional é a razão entre os totais "
        "Horvitz–Thompson estimados da categoria e dos resultados ativos, não a média simples das linhas.",
        "", "IC95% aproximado por linearização de Taylor. Para o resíduo por item e=y−R×x, "
        "t_i=(M_i/m_i)Σe_ij e v_i=M_i²(1−m_i/M_i)s²(e_ij)/m_i. "
        "V=Σ_h[N_h²(1−n_h/N_h)s²(t_i)/n_h + (N_h/n_h)Σ_i v_i]; "
        "EP(R)=sqrt(V)/X_hat. As correções de população finita e as compras sem resultados entram no cálculo. "
        "Os limites R±1,96×EP são truncados a [0,100%]. Categorias sem eventos/complementos observados "
        "não recebem IC artificial [0,0]. Domínios municipais pequenos podem ter IC impreciso; "
        "município não observado não significa ausência de contratações. Kish é somente diagnóstico dos pesos.",
        "", "## Distribuição das seis categorias", "",
        "| Porte | Resultados observados | % amostral | % ponderado | IC95% ponderado |",
        "|---|---:|---:|---:|---|"]
    for e in estimates:
        interval = f"{fmt(e['ic95_inferior'])}–{fmt(e['ic95_superior'])}%" if e["ic95_inferior"] is not None else "Não estimável"
        lines.append(f"| {e['porte']} | {counts[e['porte']]} | {fmt(100*counts[e['porte']]/len(rows) if rows else None)} | {fmt(e['percentual'])} | {interval} |")
    lines += ["", f"**{len(rows)} resultados ativos**, {s['fornecedores_identificados']} fornecedores identificados, "
        f"{s['orgaos_com_resultados']} órgãos, {s['municipios_sorteados']} municípios sorteados "
        f"({s['municipios_com_resultados']} com resultados) e {len(s['periodos_sorteados'])} meses observados.",
        "", f"Foram consultados {s['itens_sorteados']} itens; {s['itens_sem_resultado_ativo']} não têm resultado ativo. "
        f"{s['compras_sem_resultado_ativo']} compras sorteadas não têm resultados nos itens amostrados. "
        f"{s['cancelados_excluidos']} resultados cancelados foram excluídos. Esses casos não viram porte ausente.",
        "", "## Comparação por período", "",
        "| Mês de publicação | Resultados observados | Não informado ponderado | IC95% |",
        "|---|---:|---:|---|"]
    for month in [f"{YEAR}-{i:02d}" for i in range(1, 13)]:
        e = estimate(units, cells, CATEGORIES[-1], {"periodo": month})
        lines.append(f"| {month} | {e['total_amostral']} | {fmt(e['percentual'])}% | {fmt(e['ic95_inferior'])}–{fmt(e['ic95_superior'])}% |")
    lines += ["", "As tabelas por município (código IBGE), município/mês, mês, órgão e modalidade "
        "incluem as seis categorias, pesos e IC95%. municipios_observados.csv associa código e nome. "
        "por_fornecedor.csv é descritivo de registros por fornecedor, sem afirmar representatividade "
        "de fornecedores distintos (quem participa de mais itens tem maior exposição).",
        "", "## Qualidade e recomendação",
        "", f"Não informado: **{fmt(missing['percentual'])}% ponderados**, IC95% "
        f"**{fmt(missing['ic95_inferior'])}–{fmt(missing['ic95_superior'])}%**; margem observada "
        f"{fmt(missing['margem_95_pp'])} p.p. Os percentuais simples e ponderados têm denominadores diferentes.",
        "", f"Motivos na amostra: {s['motivos']}. Ausente/nulo/vazio, código 5 e inválido são classificados "
        "em Não informado, com motivo preservado. Não se aplica permanece separado. "
        f"Há {s['pf_com_porte_empresarial']} resultados PF com categoria empresarial, sinalizados para revisão.",
        "", "**Vale a pena normalizar o porte como informação complementar**, preservando código/nome original, "
        "origem, data e motivo de ausência. Manter o enriquecimento CNPJ para validação e preenchimento: "
        "preenchimento não comprova exatidão cadastral e não foi feita conferência contra CNPJ neste spike. "
        "A classificação específica MEI foi incluída no catálogo em junho de 2026; ausência de MEI em 2025 "
        "não prova ausência desses fornecedores. A recomendação não significa que a normalização já foi "
        "integrada ao pipeline principal.",
        "", "## Evidências de conclusão do PEN-005",
        "", "- População Ceará/2025 definida antes da seleção, com municípios e modalidades sem exclusão intencional.",
        "- Dimensionamento, alocação, semente, posições sorteadas e probabilidades documentados.",
        "- Consulta executada; falhas não substituídas por registros ausentes nem por compras de conveniência.",
        "- Distribuições simples e ponderadas, precisão global e recortes por município/período gerados.",
        "- Dados brutos, manifesto e unidades com itens sem resultados preservados para auditoria.",
        "", "A conclusão cobre a população definida e a data de coleta; não representa Brasil, outros anos "
        "ou contratações não publicadas no PNCP. Não há limiar de qualidade/precisão aprovado pelo produto; "
        "o IC observado explicita a precisão alcançada, sem alegar atendimento automático a ±5 p.p.",
        "", "## Reprodução e fontes",
        "", "```powershell",
        "python -m scripts.spike_pncp_representatividade --output docs/spikes/pncp-ce-2025",
        "python -m unittest discover -s tests -p test_pncp_representatividade.py",
        "```",
        "", "O mesmo diretório reproduz a fotografia em cache. Use outro diretório para nova coleta.",
        "- [Statistics Canada — Survey Methods and Practices, capítulos 6, 8 e 11](https://www150.statcan.gc.ca/n1/pub/12-587-x/12-587-x2003001-eng.pdf).",
        "- [Penn State — Multi-Stage Designs](https://online.stat.psu.edu/stat506/Lesson09).",
        "- [PNCP — modalidades](https://pncp.gov.br/api/pncp/v1/modalidades).",
        "- [PNCP — portes](https://pncp.gov.br/api/pncp/v1/portes-empresa).", ""]
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-itens", type=int, default=2)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    if args.max_itens < 2:
        parser.error("--max-itens deve ser ao menos 2 para estimar a variancia")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / ".gitignore").write_text("raw/\nrevalidacao/\n", encoding="utf-8")
    client = Client(args.output)
    manifest = {"executado_em": datetime.now(timezone.utc).isoformat(), "seed": args.seed,
                "max_itens": args.max_itens, "populacao": "Unidade compradora CE; publicacao em 2025; todas as modalidades",
                "estratos": [], "erros": [], "status": "em_coleta"}
    try:
        modalities = client.get(GESTAO + "/modalidades")
        catalog = client.get(GESTAO + "/portes-empresa")
        write_json(args.output / "modalidades.json", modalities)
        write_json(args.output / "catalogo_portes.json", catalog)
        def frame(pair):
            quarter, modality = pair
            params = frame_params(quarter, modality["id"])
            N, first = checked_page(client, params, 1)
            return {"id": f"T{quarter}-M{modality['id']:02d}", "trimestre": quarter,
                    "modalidade": modality["id"], "modalidade_nome": modality["nome"],
                    "N": N, "params": params,
                    "primeira_pagina_ids": [p["numeroControlePNCP"] for p in first]}
        pairs = [(q, modality) for q in range(1, 5) for modality in modalities]
        manifest["etapa"] = "cadastro"
        for index, cell in enumerate(parallel_results(frame, pairs, client), 1):
            manifest["estratos"].append(cell)
            write_json(args.output / "manifesto.json", manifest)
            print(f"Cadastro {index}/{len(pairs)}: {cell['id']} N={cell['N']}", flush=True)
        manifest["estratos"].sort(key=lambda cell: cell["id"])
        counts, planning = allocation({c["id"]: c["N"] for c in manifest["estratos"]})
        manifest["planejamento"] = planning
        for cell in manifest["estratos"]:
            cell["n"] = counts[cell["id"]]
        write_json(args.output / "manifesto.json", manifest)
        selection_plan = [
            {"estrato": cell["id"], "N": cell["N"], "n": cell["n"], "posicao_zero_based": position,
             "pagina": position // 50 + 1, "pi_compra": cell["n"] / cell["N"], "seed": args.seed}
            for cell in manifest["estratos"]
            for position in sorted(random.Random(f"{args.seed}:{cell['id']}:compras").sample(range(cell["N"]), cell["n"]))]
        write_csv(args.output / "plano_amostral.csv", selection_plan)
        manifest["etapa"] = "paginas_das_compras_sorteadas"
        write_json(args.output / "manifesto.json", manifest)
        selected = []
        select = lambda cell: select_purchases(client, cell, cell["n"], args.seed)
        for batch in parallel_results(select, manifest["estratos"], client):
            selected.extend(batch)
            print(f"Publicacoes sorteadas recuperadas: {len(selected)}/{planning['n_planejado']}", flush=True)
        selected.sort(key=lambda row: (row["estrato"], row["posicao_zero_based"]))
        ids = [s["publicacao"]["numeroControlePNCP"] for s in selected]
        if len(ids) != len(set(ids)):
            raise ValueError("Compra repetida entre estratos")
        write_json(args.output / "compras_sorteadas.json", selected)
        print(f"Sorteio completo: {len(selected)} compras de {planning['populacao_compras']}", flush=True)
        units = []
        manifest["etapa"] = "resultados_dos_itens"
        write_json(args.output / "manifesto.json", manifest)
        detail = lambda purchase: collect(client, purchase, args.max_itens, args.seed)
        for index, unit in enumerate(parallel_results(detail, selected, client), 1):
            units.append(unit)
            write_json(args.output / "unidades_amostrais.json", units)
            print(f"Compra {index}/{len(selected)}; resultados acumulados={sum(len(u['resultados']) for u in units)}", flush=True)
        units.sort(key=lambda u: (u["estrato"], u["compra"]))
        # Independent cache namespace: this really rechecks the frame after detail collection.
        verification = Client(args.output / "revalidacao")
        manifest["etapa"] = "revalidacao_do_cadastro"
        write_json(args.output / "manifesto.json", manifest)
        def recheck(cell):
            N, first = checked_page(verification, cell["params"], 1, cell["N"])
            if [p["numeroControlePNCP"] for p in first] != cell["primeira_pagina_ids"]:
                raise ValueError(f"Ordenacao do cadastro mudou: {cell['id']}")
            return cell["id"]
        verified = sorted(parallel_results(recheck, manifest["estratos"], verification))
        manifest["estratos_revalidados"] = verified
        summarize(args.output, units, manifest)
        print(f"Concluido: {manifest['resumo']['nao_informado']}", flush=True)
    except (Exception, KeyboardInterrupt) as error:
        manifest["status"] = "incompleto"
        manifest["erros"].append({"tipo": type(error).__name__, "mensagem": str(error)})
        write_json(args.output / "manifesto.json", manifest)
        incomplete_report(args.output, manifest)
        raise


if __name__ == "__main__":
    main()

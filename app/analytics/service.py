"""Agregacoes publicas exclusivamente de ME sobre eventos canonicos validados."""
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from .models import PublicFilters, PublicOverviewResponse, Snapshot

ORIGINS = ["BUYER_MUNICIPALITY", "OTHER_MUNICIPALITY_SAME_STATE", "OUT_OF_STATE", "UNKNOWN"]
RULE = ("Base: empenhado liquido = original + reforcos - anulacoes validas, "
        "deduplicados pela chave composta e pelo identificador do evento. "
        "Somente ME; MEI e EPP excluidos. No calculo interno MPE = ME + EPP. "
        "Participacao de ME em todas as compras e sua variacao nao sao expostas "
        "ate aprovacao de Produto/Juridico. Eventos considerados ate end_date.")

class AnalyticsError(Exception):
    def __init__(self, code, message, status=503, field=None):
        self.code, self.message, self.status, self.field = code, message, status, field
        super().__init__(message)


def brl_to_cents(value: str | Decimal | int) -> int:
    """Adaptadores devem ler JSON com parse_float=Decimal, nunca float."""
    if isinstance(value, (float, bool)):
        raise ValueError("Dinheiro deve usar decimal exato, nunca float.")
    try:
        amount = Decimal(value) * 100
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise ValueError("Valor deve ter precisao de centavos.")
        return int(amount)
    except (InvalidOperation, TypeError):
        raise ValueError("Valor monetario invalido.") from None


def previous_year(value):
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        if value.month == 2 and value.day == 29:
            return value.replace(year=value.year - 1, day=28)
        raise ValueError("Periodo anterior fora do calendario suportado.") from None


def months(start, end):
    current = start.replace(day=1)
    while current <= end:
        yield current.strftime("%Y-%m")
        if current.year == end.year and current.month == end.month:
            break
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def origin(commitment):
    if not commitment.supplier_uf:
        return "UNKNOWN"
    if commitment.supplier_uf != commitment.uf:
        return "OUT_OF_STATE"
    if not commitment.supplier_municipality_ibge_code:
        return "UNKNOWN"
    if commitment.supplier_municipality_ibge_code == commitment.municipality_ibge_code:
        return "BUYER_MUNICIPALITY"
    return "OTHER_MUNICIPALITY_SAME_STATE"


def net_cents(commitment, as_of):
    events = {}
    for event in commitment.events:
        if event.event_id in events and events[event.event_id] != event:
            raise AnalyticsError("CONFLICTING_EVENT", "Evento duplicado com valores conflitantes.")
        events[event.event_id] = event
    originals = [e for e in events.values() if e.kind == "ORIGINAL"]
    if len(originals) != 1 or originals[0].event_date != commitment.issue_date:
        raise AnalyticsError("INVALID_ORIGINAL_EVENT", "Empenho exige exatamente um evento original na emissao.")
    if any(e.event_date < commitment.issue_date for e in events.values()):
        raise AnalyticsError("INVALID_EVENT_DATE", "Evento anterior a emissao do empenho.")
    total = sum(e.amount_cents * (-1 if e.kind == "CANCELLATION" else 1)
                for e in events.values() if e.event_date <= as_of)
    if total < 0:
        raise AnalyticsError("NEGATIVE_NET_COMMITMENT", "Anomalia: empenhado liquido negativo; carga rejeitada.")
    return total


def validate_snapshot(snapshot):
    municipalities = {(m.ibge_code, m.uf): m for m in snapshot.municipalities}
    if len({(m.tce_municipality_code, m.uf) for m in snapshot.municipalities}) != len(municipalities):
        raise AnalyticsError("INVALID_MUNICIPALITY_MAPPING", "Codigo TCE associado a mais de um municipio na UF.")
    if len(municipalities) != len(snapshot.municipalities):
        raise AnalyticsError("DUPLICATE_MUNICIPALITY", "Dimensao municipal duplicada.")
    coverage = {}
    for entry in snapshot.coverage:
        key = (entry.municipality_ibge_code, entry.uf, entry.month)
        if key in coverage or key[:2] not in municipalities:
            raise AnalyticsError("INVALID_COVERAGE", "Cobertura duplicada ou municipio desconhecido.")
        if entry.month > snapshot.data_as_of.strftime("%Y-%m") and entry.status != "NO_DATA":
            raise AnalyticsError("INVALID_COVERAGE", "Cobertura posterior a data da carga.")
        coverage[key] = entry.status
    distinct = {}
    for row in snapshot.commitments:
        municipality = municipalities.get((row.municipality_ibge_code, row.uf))
        if not municipality or municipality.tce_municipality_code != row.tce_municipality_code:
            raise AnalyticsError("INVALID_MUNICIPALITY_MAPPING", "Chave IBGE + UF inconsistente com codigo TCE.")
        if row.expense_element_code not in snapshot.expense_elements:
            raise AnalyticsError("UNKNOWN_EXPENSE_ELEMENT", "Elemento fora da dimensao canonica.")
        if row.issue_date > snapshot.data_as_of or any(e.event_date > snapshot.data_as_of for e in row.events):
            raise AnalyticsError("INVALID_EVENT_DATE", "Evento posterior a data da carga.")
        if row.key in distinct and distinct[row.key] != row:
            raise AnalyticsError("CONFLICTING_COMMITMENT", "Empenho duplicado com dados conflitantes.")
        if coverage.get((row.municipality_ibge_code, row.uf, row.issue_date.strftime("%Y-%m")), "NO_DATA") == "NO_DATA":
            raise AnalyticsError("INVALID_COVERAGE", "Empenho em mes declarado sem carga.")
        net_cents(row, snapshot.data_as_of)
        distinct[row.key] = row
    return list(distinct.values()), coverage


def build_overview(snapshot: Snapshot, filters: PublicFilters, request_id: str):
    rows, coverage = validate_snapshot(snapshot)
    municipalities = [m for m in snapshot.municipalities if m.uf == filters.uf
                      and (not filters.municipality_ibge_code or m.ibge_code == filters.municipality_ibge_code)]
    if not municipalities:
        raise AnalyticsError("MUNICIPALITY_NOT_FOUND", "Municipio ou UF inexistente no escopo.", 404)
    codes = list(dict.fromkeys(filters.expense_element_codes or sorted(snapshot.expense_elements)))
    if set(codes) - snapshot.expense_elements.keys():
        raise AnalyticsError("INVALID_EXPENSE_ELEMENT", "Elemento de despesa fora do escopo.", 422, "expense_element_codes")
    origins = list(dict.fromkeys(filters.supplier_origins or ORIGINS))
    applied = filters.model_copy(update={"expense_element_codes": codes, "supplier_origins": origins})
    scope = {(m.ibge_code, m.uf) for m in municipalities}
    # Filter ME BEFORE every public aggregation. Do not compute the all-size total.
    eligible = [r for r in rows if r.company_size == "ME" and not r.is_mei
                and (r.municipality_ibge_code, r.uf) in scope
                and r.expense_element_code in codes and origin(r) in origins]

    def select(start, end):
        return [(r, net_cents(r, min(end, snapshot.data_as_of))) for r in eligible
                if start <= r.issue_date <= min(end, snapshot.data_as_of)]

    current = select(filters.start_date, filters.end_date)
    prior_start, prior_end = previous_year(filters.start_date), previous_year(filters.end_date)
    previous = select(prior_start, prior_end)
    total = sum(v for _, v in current)
    prior_total = sum(v for _, v in previous)

    def status(month, municipal_scope=scope):
        states = [coverage.get((ibge, uf, month), "NO_DATA") for ibge, uf in municipal_scope]
        if all(s == "NO_DATA" for s in states):
            return "NO_DATA"
        if snapshot.is_stale or "STALE" in states:
            return "STALE"
        month_end = date.fromisoformat(month + "-01").replace(day=monthrange(int(month[:4]), int(month[5:]))[1])
        if month_end > snapshot.data_as_of or any(s != "COMPLETE" for s in states):
            return "PARTIAL"
        return "COMPLETE"

    current_months = list(months(filters.start_date, filters.end_date))
    complete = all(status(m) == "COMPLETE" for m in current_months) and filters.end_date <= snapshot.data_as_of
    prior_complete = all(status(m) == "COMPLETE" for m in months(prior_start, prior_end))
    is_stale = snapshot.is_stale or any(status(m) == "STALE" for m in current_months)
    warnings = []
    if not complete:
        warnings.append("Cobertura parcial, ausente ou desatualizada; totais limitados aos registros disponiveis.")
    if any(not r.historical_size_classification for r, _ in current):
        warnings.append("Parte do porte cadastral nao foi confirmada na data do empenho.")
    by_origin = {o: sum(v for r, v in current if origin(r) == o) for o in ORIGINS}
    local = ratio(by_origin["BUYER_MUNICIPALITY"], total)
    evasion = ratio(by_origin["OTHER_MUNICIPALITY_SAME_STATE"] + by_origin["OUT_OF_STATE"], total)
    elements = []
    for code in sorted({r.expense_element_code for r, _ in current}):
        records = [(r, v) for r, v in current if r.expense_element_code == code]
        amount = sum(v for _, v in records)
        elements.append(dict(code=code, name=snapshot.expense_elements[code], me_committed_net_cents=amount,
                             share_of_me_rate=ratio(amount, total), commitments_count=len(records)))
    series = []
    for month in current_months:
        state = status(month)
        prior_month = str(int(month[:4]) - 1) + month[4:]
        series.append(dict(month=month, coverage_status=state,
                           me_committed_net_cents=None if state == "NO_DATA" else sum(v for r, v in current if r.issue_date.strftime("%Y-%m") == month),
                           previous_period_me_committed_net_cents=None if status(prior_month) != "COMPLETE" else sum(v for r, v in previous if r.issue_date.strftime("%Y-%m") == prior_month)))
    municipal_items = []
    for municipality in municipalities:
        records = [(r, v) for r, v in current if r.municipality_ibge_code == municipality.ibge_code]
        amount = sum(v for _, v in records)
        available = any(status(m, {(municipality.ibge_code, municipality.uf)}) != "NO_DATA" for m in current_months)
        municipal_items.append(dict(ibge_code=municipality.ibge_code, name=municipality.name, uf=municipality.uf,
                                    region=municipality.region, me_committed_net_cents=amount,
                                    # State share unavailable when the query contains only one municipality.
                                    share_of_state_me_rate=ratio(amount, total) if not filters.municipality_ibge_code else None,
                                    local_me_purchases_rate=ratio(sum(v for r, v in records if origin(r) == "BUYER_MUNICIPALITY"), amount),
                                    municipal_evasion_me_rate=ratio(sum(v for r, v in records if origin(r) in ORIGINS[1:3]), amount),
                                    commitments_count=len(records), data_available=available))
    utc = lambda value: value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return PublicOverviewResponse(
        meta=dict(generated_at=utc(datetime.now(timezone.utc)), data_as_of=snapshot.data_as_of,
                  sources=snapshot.sources, request_id=request_id, is_stale=is_stale,
                  last_successful_load_at=utc(snapshot.last_successful_load_at),
                  stale_reason=(snapshot.stale_reason or "STALE_COVERAGE") if is_stale else None,
                  warnings=warnings, calculation_rule=RULE),
        filters_applied=applied,
        kpis=dict(me_committed_net_cents=total, local_me_purchases_rate=local,
                  municipal_evasion_me_rate=evasion, me_commitments_count=len(current),
                  identified_me_suppliers_count=len({r.supplier_cnpj for r, _ in current if r.supplier_cnpj}),
                  me_committed_net_change_rate=(total - prior_total) / abs(prior_total) if complete and prior_complete and prior_total else None),
        top_expense_elements=sorted(elements, key=lambda e: (-e["me_committed_net_cents"], e["code"]))[:5],
        monthly_series=series, expense_elements=elements,
        supplier_origins=[dict(origin=o, me_committed_net_cents=by_origin[o], share_of_me_rate=ratio(by_origin[o], total))
                          for o in origins] if current else [],
        municipalities=municipal_items,
        quality=dict(municipalities_with_data=sum(m["data_available"] for m in municipal_items),
                     municipalities_in_scope=len(municipalities), unknown_supplier_origin_rate=ratio(by_origin["UNKNOWN"], total),
                     historical_size_classification_rate=ratio(sum(r.historical_size_classification for r, _ in current), len(current)),
                     is_partial_period=not complete, warnings=warnings))

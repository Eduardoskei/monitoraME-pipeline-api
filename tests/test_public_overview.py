from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import os
import unittest
from unittest.mock import patch

# Same isolated environment as the existing KPI tests.
from tests import test_kpis  # noqa: F401
from fastapi import FastAPI
from pydantic import ValidationError
from app.analytics.models import PublicFilters, Snapshot
from app.analytics.repository import load_snapshot
from app.analytics.service import AnalyticsError, brl_to_cents, build_overview, net_cents, previous_year
from app.api.endpoints import analysis, pipeline


def fixture():
    municipality = {"ibge_code": "2300200", "tce_municipality_code": "010", "name": "Amontada", "uf": "CE"}
    def commitment(number, size, amount):
        return {"municipality_ibge_code": "2300200", "uf": "CE", "tce_municipality_code": "010",
                "budget_year": 2025, "agency_code": "01", "budget_unit_code": "01",
                "issue_date": "2025-01-15", "commitment_number": number,
                "supplier_cnpj": "11444777000161", "company_size": size,
                "historical_size_classification": True,
                "supplier_municipality_ibge_code": "2300200", "supplier_uf": "CE",
                "expense_element_code": "339030",
                "events": [{"event_id": "original", "kind": "ORIGINAL", "event_date": "2025-01-15", "amount_cents": amount}]}
    return {"schema_version": "0.9.1", "event_dictionary_version": "test-fixture-v1",
            "data_as_of": "2025-01-31", "last_successful_load_at": "2025-02-01T12:00:00Z",
            "sources": ["TCE_CE_SIM"], "municipalities": [municipality],
            "expense_elements": {"339030": "Material de consumo"},
            "coverage": [{"municipality_ibge_code": "2300200", "uf": "CE", "month": "2025-01", "status": "COMPLETE"}],
            "commitments": [commitment("1", "ME", 20000000), commitment("2", "MEI", 10000000),
                            commitment("3", "EPP", 15000000), commitment("4", "UNKNOWN", 55000000)]}


def filters(**changes):
    return PublicFilters(start_date=changes.pop("start_date", "2025-01-01"),
                         end_date=changes.pop("end_date", "2025-01-31"), **changes)


def build(data=None, **changes):
    return build_overview(Snapshot.model_validate(data or fixture()), filters(**changes), "req_test").model_dump(mode="json")


async def http_get(app, query="start_date=2025-01-01&end_date=2025-01-31", path="/api/v1/analysis/public/overview"):
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
               "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
               "root_path": "", "headers": [], "client": ("127.0.0.1", 123), "server": ("test", 80)}, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], json.loads(body), dict(start["headers"])


class PublicCalculationTest(unittest.TestCase):
    def test_only_me_and_public_gate(self):
        result = build()
        self.assertEqual(result["kpis"]["me_committed_net_cents"], 20000000)
        self.assertEqual(result["kpis"]["me_commitments_count"], 1)
        self.assertIsNone(result["kpis"]["me_share_of_all_purchases_rate"])
        self.assertIsNone(result["kpis"]["me_share_change_pp"])
        self.assertEqual(result["kpis"]["me_share_of_all_purchases_status"], "PENDING_PRODUCT_LEGAL_APPROVAL")
        serialized = json.dumps(result)
        for forbidden in ("total_cents", "total_committed_net_cents", "mei_rate", "epp_rate", "supplier_cnpj"):
            self.assertNotIn('"' + forbidden + '"', serialized)
        self.assertEqual(result["meta"]["company_sizes"], ["ME"])
        self.assertNotIn("company_sizes", result["filters_applied"])
        self.assertEqual(result["filters_applied"]["expense_element_codes"], ["339030"])
        self.assertEqual(len(result["filters_applied"]["supplier_origins"]), 4)

    def test_flag_mei_overrides_me(self):
        data = fixture()
        data["commitments"][0]["is_mei"] = True
        self.assertEqual(build(data)["kpis"]["me_committed_net_cents"], 0)

    def test_net_reinforcements_cancellations_and_duplicates(self):
        data = fixture()
        row = data["commitments"][0]
        row["events"] += [
            {"event_id": "r1", "kind": "REINFORCEMENT", "event_date": "2025-01-16", "amount_cents": 5000},
            {"event_id": "a1", "kind": "CANCELLATION", "event_date": "2025-01-17", "amount_cents": 2000}]
        row["events"].append(deepcopy(row["events"][-1]))
        data["commitments"].append(deepcopy(row))
        result = build(data)
        self.assertEqual(result["kpis"]["me_committed_net_cents"], 20003000)
        self.assertEqual(result["kpis"]["me_commitments_count"], 1)

    def test_composite_key_prevents_collisions(self):
        data = fixture()
        other = deepcopy(data["commitments"][0])
        other["agency_code"] = "02"
        data["commitments"].append(other)
        self.assertEqual(build(data)["kpis"]["me_commitments_count"], 2)
        data["commitments"][-1]["agency_code"] = "01"
        data["commitments"][-1]["events"][0]["amount_cents"] += 1
        with self.assertRaisesRegex(AnalyticsError, "conflitantes"):
            build(data)

    def test_negative_net_is_anomaly_not_zero(self):
        data = fixture()
        data["commitments"][0]["events"].append({"event_id": "a", "kind": "CANCELLATION", "event_date": "2025-01-16", "amount_cents": 20000001})
        with self.assertRaises(AnalyticsError) as raised:
            build(data)
        self.assertEqual(raised.exception.code, "NEGATIVE_NET_COMMITMENT")

    def test_exact_money(self):
        self.assertEqual(brl_to_cents("90071992547409.93"), 9007199254740993)
        self.assertEqual(brl_to_cents(Decimal("0.29")), 29)
        for value in (.29, "0.001", "NaN", True):
            with self.assertRaises(ValueError):
                brl_to_cents(value)
        data = fixture()
        data["commitments"][0]["events"][0]["amount_cents"] = 1.5
        with self.assertRaises(ValidationError):
            Snapshot.model_validate(data)

    def test_dates_inclusive_and_future_events_excluded(self):
        data = fixture()
        data["commitments"][0]["events"].append({"event_id": "a", "kind": "CANCELLATION", "event_date": "2025-01-16", "amount_cents": 100})
        self.assertEqual(build(data, start_date="2025-01-15", end_date="2025-01-15")["kpis"]["me_committed_net_cents"], 20000000)
        self.assertEqual(build(data, start_date="2025-01-16")["kpis"]["me_commitments_count"], 0)
        self.assertEqual(previous_year(date(2024, 2, 29)), date(2023, 2, 28))
        with self.assertRaises(ValidationError):
            filters(start_date="20250101")
        with self.assertRaises(ValidationError):
            filters(start_date="2025-02-01")

    def test_no_load_month_is_null_and_missing_previous_is_null(self):
        result = build(end_date="2025-02-28")
        self.assertEqual(result["monthly_series"][1]["coverage_status"], "NO_DATA")
        self.assertIsNone(result["monthly_series"][1]["me_committed_net_cents"])
        self.assertIsNone(result["kpis"]["me_committed_net_change_rate"])
        self.assertTrue(result["quality"]["is_partial_period"])

    def test_origins_unknown_is_not_evasion(self):
        data = fixture()
        data["commitments"][0]["supplier_uf"] = None
        result = build(data)
        self.assertEqual(result["quality"]["unknown_supplier_origin_rate"], 1)
        self.assertEqual(result["kpis"]["municipal_evasion_me_rate"], 0)
        self.assertEqual(sum(o["me_committed_net_cents"] for o in result["supplier_origins"]), result["kpis"]["me_committed_net_cents"])

    def test_empty_and_zero(self):
        data = fixture()
        data["commitments"] = []
        result = build(data)
        self.assertEqual(result["expense_elements"], [])
        self.assertEqual(result["supplier_origins"], [])
        self.assertIsNone(result["kpis"]["local_me_purchases_rate"])
        self.assertEqual(result["kpis"]["me_committed_net_cents"], 0)

    def test_previous_year_change_requires_complete_coverage(self):
        data = fixture()
        prior = deepcopy(data["commitments"][0])
        prior.update(issue_date="2024-01-15", budget_year=2024)
        prior["events"][0].update(event_date="2024-01-15", amount_cents=10000000)
        data["commitments"].append(prior)
        data["coverage"].append({"municipality_ibge_code": "2300200", "uf": "CE", "month": "2024-01", "status": "COMPLETE"})
        result = build(data)
        self.assertEqual(result["kpis"]["me_committed_net_change_rate"], 1)
        self.assertEqual(result["monthly_series"][0]["previous_period_me_committed_net_cents"], 10000000)
        data["coverage"][-1]["status"] = "PARTIAL"
        self.assertIsNone(build(data)["kpis"]["me_committed_net_change_rate"])

    def test_wrong_cancellation_key_does_not_change_other_commitment(self):
        data = fixture()
        # A different agency with the same note number is a separate commitment.
        other = deepcopy(data["commitments"][0])
        other["agency_code"] = "02"
        other["events"].append({"event_id": "a1", "kind": "CANCELLATION", "event_date": "2025-01-15", "amount_cents": 100})
        data["commitments"].append(other)
        self.assertEqual(build(data)["kpis"]["me_committed_net_cents"], 39999900)
        row = Snapshot.model_validate(data).commitments[0]
        self.assertEqual(net_cents(row, date(2025, 1, 31)), 20000000)

    def test_filter_expansion_and_rejection(self):
        self.assertEqual(build()["filters_applied"], build(expense_element_codes=[], supplier_origins=[])["filters_applied"])
        with self.assertRaises(AnalyticsError) as raised:
            build(expense_element_codes=["999999"])
        self.assertEqual(raised.exception.status, 422)
        with self.assertRaises(AnalyticsError) as raised:
            build(municipality_ibge_code="2300309")
        self.assertEqual(raised.exception.status, 404)


class PublicHttpTest(unittest.TestCase):
    def setUp(self):
        analysis._clients.clear()
        self.app = FastAPI()
        self.app.include_router(analysis.router)
        self.app.include_router(pipeline.router)

    def test_http_contract_and_encoding(self):
        with patch.object(analysis, "load_snapshot", return_value=Snapshot.model_validate(fixture())):
            status, body, headers = asyncio.run(http_get(self.app))
        self.assertEqual(status, 200)
        self.assertEqual(body["response_type"], "public_overview")
        self.assertEqual(headers[b"content-type"], b"application/json; charset=utf-8")
        self.assertTrue(body["meta"]["generated_at"].endswith("Z"))
        self.assertEqual(body["meta"]["source_timezone"], "America/Fortaleza")

    def test_sizes_are_rejected_even_empty_before_load(self):
        with patch.object(analysis, "load_snapshot") as load:
            for query in ("company_size=", "company_sizes=", "company_sizes=[]", "company_sizes=ME"):
                status, body, _ = asyncio.run(http_get(self.app, query))
                self.assertEqual(status, 422)
                self.assertEqual(body["error"]["code"], "COMPANY_SIZE_NOT_ALLOWED")
            load.assert_not_called()

    def test_invalid_dates_and_unknown_parameters(self):
        for query in ("start_date=20250101&end_date=2025-01-31", "start_date=2025-02-01&end_date=2025-01-31",
                      "start_date=2025-01-01&end_date=2025-01-31&unexpected=1", ""):
            status, body, _ = asyncio.run(http_get(self.app, query))
            self.assertEqual(status, 422)
            self.assertIsInstance(body["error"]["details"], list)

    def test_no_snapshot_and_rate_limit(self):
        with patch.dict(os.environ, {"ANALYTICS_SNAPSHOT_PATH": "", "ANALYTICS_PREVIOUS_SNAPSHOT_PATH": ""}):
            status, body, _ = asyncio.run(http_get(self.app))
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "NO_VALID_LOAD")
        with patch.object(analysis, "rate_allowed", return_value=False):
            status, _, headers = asyncio.run(http_get(self.app))
        self.assertEqual(status, 429)
        self.assertIn(b"retry-after", headers)

    def test_old_contract_indicator_no_longer_exposes_rates(self):
        status, _, _ = asyncio.run(http_get(self.app, path="/pipeline/tce/kpis/me-por-mes"))
        self.assertEqual(status, 410)

    def test_empty_arrays_expand_through_http(self):
        with patch.object(analysis, "load_snapshot", return_value=Snapshot.model_validate(fixture())):
            status, body, _ = asyncio.run(http_get(self.app, "start_date=2025-01-01&end_date=2025-01-31&expense_element_codes=[]&supplier_origins=[]"))
        self.assertEqual(status, 200)
        self.assertEqual(body["filters_applied"]["expense_element_codes"], ["339030"])
        self.assertEqual(len(body["filters_applied"]["supplier_origins"]), 4)

    def test_limiter_window(self):
        with patch.object(analysis, "monotonic", return_value=100):
            self.assertTrue(all(analysis.rate_allowed("test") for _ in range(60)))
            self.assertFalse(analysis.rate_allowed("test"))
        with patch.object(analysis, "monotonic", return_value=161):
            self.assertTrue(analysis.rate_allowed("test"))

    def test_previous_snapshot_fallback(self):
        with patch.dict(os.environ, {"ANALYTICS_SNAPSHOT_PATH": "current", "ANALYTICS_PREVIOUS_SNAPSHOT_PATH": "previous"}), \
             patch("app.analytics.repository.Path.read_bytes", side_effect=[OSError(), json.dumps(fixture()).encode()]):
            loaded = load_snapshot()
        self.assertTrue(loaded.is_stale)
        self.assertEqual(loaded.stale_reason, "CURRENT_LOAD_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()

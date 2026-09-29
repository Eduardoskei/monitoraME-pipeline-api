"""Contrato publico 0.9.1 e entrada canonica de empenhos (nao contratos)."""
from datetime import date, datetime, timezone
from typing import Annotated, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

CompanySize = Literal["ME", "EPP", "MEI", "OTHER", "UNKNOWN"]
Origin = Literal["BUYER_MUNICIPALITY", "OTHER_MUNICIPALITY_SAME_STATE", "OUT_OF_STATE", "UNKNOWN"]
Coverage = Literal["COMPLETE", "PARTIAL", "NO_DATA", "STALE"]
Ibge = Annotated[str, Field(pattern=r"^[0-9]{7}$")]
Cents = Annotated[StrictInt, Field(ge=0)]
Rate = Annotated[float, Field(ge=0, le=1)]

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

class PublicFilters(Model):
    uf: str = Field(default="CE", pattern=r"^[A-Z]{2}$")
    municipality_ibge_code: Ibge | None = None
    start_date: date
    end_date: date
    expense_element_codes: list[Annotated[str, Field(pattern=r"^[0-9]{6}$")]] = Field(default_factory=list)
    supplier_origins: list[Origin] = Field(default_factory=list)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def iso_dates(cls, value):
        if isinstance(value, str):
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError("Use YYYY-MM-DD.")
        elif not isinstance(value, date) or isinstance(value, datetime):
            raise ValueError("Use YYYY-MM-DD.")
        return value

    @model_validator(mode="after")
    def ordered(self):
        if self.start_date.year == 1:
            raise ValueError("Periodo anterior fora do calendario suportado.")
        if self.start_date > self.end_date:
            raise ValueError("start_date deve ser menor ou igual a end_date.")
        return self

class Municipality(Model):
    ibge_code: Ibge
    tce_municipality_code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    uf: str = Field(pattern=r"^[A-Z]{2}$")
    region: str | None = None

class Event(Model):
    event_id: str = Field(min_length=1)
    kind: Literal["ORIGINAL", "REINFORCEMENT", "CANCELLATION"]
    event_date: date
    amount_cents: Cents

class Commitment(Model):
    municipality_ibge_code: Ibge
    uf: str = Field(pattern=r"^[A-Z]{2}$")
    tce_municipality_code: str = Field(min_length=1)
    budget_year: int = Field(ge=1900, le=9999)
    agency_code: str = Field(min_length=1)
    budget_unit_code: str = Field(min_length=1)
    issue_date: date
    commitment_number: str = Field(min_length=1)
    supplier_cnpj: Annotated[str, Field(pattern=r"^[0-9]{14}$")] | None = None
    company_size: CompanySize
    is_mei: bool = False
    historical_size_classification: bool
    supplier_municipality_ibge_code: Ibge | None = None
    supplier_uf: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    expense_element_code: str = Field(pattern=r"^[0-9]{6}$")
    events: list[Event] = Field(min_length=1)

    @property
    def key(self):
        return (self.tce_municipality_code, self.budget_year, self.agency_code,
                self.budget_unit_code, self.issue_date, self.commitment_number)

class MonthCoverage(Model):
    municipality_ibge_code: Ibge
    uf: str = Field(pattern=r"^[A-Z]{2}$")
    month: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    status: Coverage

class Snapshot(Model):
    schema_version: Literal["0.9.1"]
    # Explicit attestation by the ingestion adapter; raw TCE rows are rejected.
    event_dictionary_version: str = Field(min_length=1)
    data_as_of: date
    last_successful_load_at: datetime
    sources: list[str] = Field(min_length=1)
    is_stale: bool = False
    stale_reason: str | None = None
    municipalities: list[Municipality]
    expense_elements: dict[Annotated[str, Field(pattern=r"^[0-9]{6}$")], str] = Field(min_length=1)
    coverage: list[MonthCoverage]
    commitments: list[Commitment]

    @field_validator("last_successful_load_at")
    @classmethod
    def utc_load(cls, value):
        if value.tzinfo is None:
            raise ValueError("Horario da carga deve incluir fuso.")
        return value.astimezone(timezone.utc)

class PublicMeta(Model):
    schema_version: Literal["0.9.1"] = "0.9.1"
    generated_at: AwareDatetime
    data_as_of: date
    currency: Literal["BRL"] = "BRL"
    money_unit: Literal["CENTS"] = "CENTS"
    data_scope: Literal["PUBLIC_ME_ONLY"] = "PUBLIC_ME_ONLY"
    company_sizes: list[Literal["ME"]] = Field(default_factory=lambda: ["ME"], min_length=1, max_length=1)
    sources: list[str]
    request_id: str
    source_timezone: Literal["America/Fortaleza"] = "America/Fortaleza"
    is_stale: bool
    last_successful_load_at: AwareDatetime
    stale_reason: str | None
    warnings: list[str] = Field(default_factory=list)
    calculation_rule: str

class PublicKpis(Model):
    me_committed_net_cents: Cents
    me_share_of_all_purchases_rate: None = None
    me_share_of_all_purchases_status: Literal["PENDING_PRODUCT_LEGAL_APPROVAL"] = "PENDING_PRODUCT_LEGAL_APPROVAL"
    local_me_purchases_rate: Rate | None
    municipal_evasion_me_rate: Rate | None
    me_commitments_count: int
    identified_me_suppliers_count: int
    me_committed_net_change_rate: float | None
    me_share_change_pp: None = None

class ElementAggregate(Model):
    code: str
    name: str
    me_committed_net_cents: Cents
    share_of_me_rate: Rate | None
    commitments_count: int

class MonthlyPoint(Model):
    month: str
    me_committed_net_cents: Cents | None
    previous_period_me_committed_net_cents: Cents | None
    coverage_status: Coverage

class OriginAggregate(Model):
    origin: Origin
    me_committed_net_cents: Cents
    share_of_me_rate: Rate | None

class MunicipalityAggregate(Model):
    ibge_code: Ibge
    name: str
    uf: str
    region: str | None
    me_committed_net_cents: Cents
    share_of_state_me_rate: Rate | None
    local_me_purchases_rate: Rate | None
    municipal_evasion_me_rate: Rate | None
    commitments_count: int
    data_available: bool

class Quality(Model):
    municipalities_with_data: int
    municipalities_in_scope: int
    unknown_supplier_origin_rate: Rate | None
    historical_size_classification_rate: Rate | None
    is_partial_period: bool
    warnings: list[str]

class PublicOverviewResponse(Model):
    response_type: Literal["public_overview"] = "public_overview"
    meta: PublicMeta
    filters_applied: PublicFilters
    kpis: PublicKpis
    top_expense_elements: list[ElementAggregate]
    monthly_series: list[MonthlyPoint]
    expense_elements: list[ElementAggregate]
    supplier_origins: list[OriginAggregate]
    municipalities: list[MunicipalityAggregate]
    quality: Quality

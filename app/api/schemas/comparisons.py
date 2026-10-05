from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ComparisonMode(StrEnum):
    PERIODS = "PERIODS"
    MUNICIPALITIES = "MUNICIPALITIES"
    MIXED = "MIXED"


class CompanySize(StrEnum):
    ME = "ME"
    MEI = "MEI"
    EPP = "EPP"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class SupplierOrigin(StrEnum):
    NO_MUNICIPIO_COMPRADOR = "NO_MUNICIPIO_COMPRADOR"
    EM_OUTRO_MUNICIPIO = "EM_OUTRO_MUNICIPIO"
    FORA_DO_CEARA = "FORA_DO_CEARA"
    ORIGEM_NAO_IDENTIFICADA = "ORIGEM_NAO_IDENTIFICADA"


class ComparisonFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uf: str = Field(min_length=2, max_length=2)
    municipality_ibge_code: str = Field(pattern=r"^\d{7}$")
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    company_sizes: list[CompanySize] = Field(default_factory=list)
    supplier_origins: list[SupplierOrigin] = Field(default_factory=list)
    expense_element_codes: list[str] = Field(default_factory=list)


class ComparisonSide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    filters: ComparisonFilters


class TceComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_mode: ComparisonMode
    left: ComparisonSide
    right: ComparisonSide


__all__ = [
    "CompanySize",
    "ComparisonFilters",
    "ComparisonMode",
    "ComparisonSide",
    "SupplierOrigin",
    "TceComparisonRequest",
]

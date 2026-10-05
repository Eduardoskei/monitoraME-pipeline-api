from __future__ import annotations

from enum import StrEnum


class SupplierDocumentType(StrEnum):
    CNPJ = "CNPJ"
    CPF = "CPF"


class SupplierSortField(StrEnum):
    SUPPLIER = "supplier"
    GROSS_VALUE = "gross_value"
    CANCELLED_VALUE = "cancelled_value"
    NET_VALUE = "net_value"
    COMMITMENTS_COUNT = "commitments_count"
    FIRST_COMMITMENT = "first_commitment"
    LAST_COMMITMENT = "last_commitment"


__all__ = ["SupplierDocumentType", "SupplierSortField"]

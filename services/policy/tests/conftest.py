"""Shared fixtures for policy-service tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.context import PolicyInputs
from app.packs import PolicyPack, load_pack


@pytest.fixture(scope="session")
def std_pack() -> PolicyPack:
    return load_pack("PF-STD", "2026.09.1")


@pytest.fixture(scope="session")
def shariah_pack() -> PolicyPack:
    return load_pack("PF-SHARIAH", "2026.09.1")


def clean_inputs(**overrides: object) -> PolicyInputs:
    """A case that passes every PF-STD gate. Tests break one thing at a time."""
    base = {
        "member_status": "ACTIVE",
        "member_tenure_months": 110,
        "member_age": 41,
        "member_total_exposure": Decimal("0"),
        "member_grade": "B",
        "identity_verified": True,
        "requested_amount": Decimal("8000"),
        "requested_tenor": 24,
        "requested_purpose": "EDUCATION",
        "documents_required_complete": True,
        "documents_min_critical_confidence": 0.94,
        "documents_present": ("IDENTITY", "PAYSLIP_LATEST_3", "EMPLOYMENT_CONFIRMATION"),
        "document_confidence": {"IDENTITY": 0.97, "PAYSLIP_LATEST_3": 0.94, "EMPLOYMENT_CONFIRMATION": 0.96},
        "income_verified": True,
        "income_verified_monthly": Decimal("4200"),
        "commitments_monthly": Decimal("600"),
    }
    base.update(overrides)
    return PolicyInputs(**base)  # type: ignore[arg-type]


@pytest.fixture
def clean() -> PolicyInputs:
    return clean_inputs()

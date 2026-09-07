"""Golden case fixtures (docs/12 §6, docs/11 §1).

Five cases the Council can be run against without any service being up, each
carrying both the snapshot an agent would see and the tool results it would
get. They exist so that agent behaviour can be checked deterministically:
whether a stance follows from the evidence, whether a factor score equals the
tool that produced it, whether a claim cites something real.

They are hand-built rather than sampled. A sampled case measures whatever the
generator happened to produce; these are chosen to be the shapes the platform
must handle -- clean, discrepant, tampered, over the affordability limit, and
inside a guarantee ring -- which are the first five scenarios in docs/11 §1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["GOLDEN", "GoldenCase", "load_golden", "write_golden"]

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "synthetic" / "golden"


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One case, with everything an agent would be given about it."""

    case_id: str
    title: str
    scenario: str
    snapshot: dict[str, Any]
    tool_results: dict[str, Any]
    expected: dict[str, Any] = field(default_factory=dict)

    def results_for(self, tools: list[str]) -> list[dict[str, Any]]:
        """The tool results an agent holding these grants would have."""
        return [self.tool_results[name] for name in tools if name in self.tool_results]

    def evidence_ids(self) -> set[str]:
        found: set[str] = set()
        for result in self.tool_results.values():
            for ref in result.get("evidence_refs") or []:
                found.add(str(ref.get("evidence_id") if isinstance(ref, dict) else ref))
        return found

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "scenario": self.scenario,
            "snapshot": self.snapshot,
            "tool_results": self.tool_results,
            "expected": self.expected,
        }


def _evidence(prefix: str, index: int) -> str:
    """A well-formed evidence id, stable across runs so tests can name one."""
    body = f"{index:026d}".replace("0", "0")
    return f"ev_{body[:26].upper().replace('0', '0')}"


def _ev(n: int) -> str:
    # Crockford base32 has no I, L, O or U; padding with zeros is safe.
    return "ev_" + f"{n:026X}".replace("I", "1").replace("L", "1").replace("O", "0").replace("U", "V")


def _result(tool: str, payload: Any, *evidence: str) -> dict[str, Any]:
    return {"tool": tool, "result": payload, "evidence_refs": [{"evidence_id": e} for e in evidence]}


#: The five people the demo is about, one per scenario.
#:
#: A ledger row reading «MEMBER_1» is correct — that is the masked reference an
#: agent sees, and masking before a provider call is the point — but it made
#: every case in the queue look like the same member, and nobody watching a
#: demonstration can follow five applications from one person. Each scenario
#: now names a member who exists in the core, with an account and an
#: application behind them, so the queue reads like a queue.
#:
#: `member_ref` stays masked. What changes is which real member a case belongs
#: to, not what the council is allowed to see.
DEMO_MEMBERS: dict[str, str] = {
    "case_S1CLEAN": "M-000028",
    "case_S2INCOME": "M-000031",
    "case_S3TAMPER": "M-000032",
    "case_S4BREACH": "M-000035",
    "case_S5RING": "M-000038",
}


def _snapshot(
    case_id: str,
    *,
    amount: str,
    tenor: int,
    tenure: int,
    product: str = "PF-STD",
    documents: list[dict[str, Any]] | None = None,
    purpose: str = "HOME_IMPROVEMENT",
) -> dict[str, Any]:
    return {
        "snapshot_id": f"snap_{case_id[5:].upper():0>26}"[:31],
        "case_id": case_id,
        "product_code": product,
        "amount": amount,
        "tenor_months": tenor,
        "purpose": purpose,
        "policy_version": f"policy/{product}/2026.09.1",
        "dff_version": f"dff/{product}/2026.09.1",
        "autonomy_version": f"autonomy/{product}/2026.09.1",
        "model_versions": {"risk": "2026.09.1", "fraud": "fraud-rules/1.0"},
        "member": {
            "member_ref": "«MEMBER_1»",
            # Who the case is actually about. Masked for the council, named for
            # the ledger and the screens a person reads.
            "member_id": DEMO_MEMBERS.get(case_id),
            "tenure_months": tenure,
            "branch_id": "BR-01",
            "employer_sector": "PUBLIC_ADMIN",
        },
        "documents": documents
        or [
            {"document_id": "doc_identity", "type": "IDENTITY", "status": "EXTRACTED", "confidence": 0.96},
            {
                "document_id": "doc_payslip",
                "type": "PAYSLIP_LATEST_3",
                "status": "EXTRACTED",
                "confidence": 0.94,
            },
            {
                "document_id": "doc_letter",
                "type": "EMPLOYMENT_CONFIRMATION",
                "status": "EXTRACTED",
                "confidence": 0.92,
            },
        ],
    }


def _clean_case() -> GoldenCase:
    """docs/11 S1 — a case with nothing wrong with it."""
    case_id = "case_S1CLEAN"
    return GoldenCase(
        case_id=case_id,
        title="Clean application, fast path",
        scenario="S1",
        snapshot=_snapshot(case_id, amount="8000.00", tenor=36, tenure=108),
        tool_results={
            "documents.list": _result(
                "documents.list",
                [
                    {
                        "document_id": "doc_identity",
                        "type": "IDENTITY",
                        "status": "EXTRACTED",
                        "classified_conf": 0.96,
                        "pages": 1,
                    },
                    {
                        "document_id": "doc_payslip",
                        "type": "PAYSLIP_LATEST_3",
                        "status": "EXTRACTED",
                        "classified_conf": 0.94,
                        "pages": 1,
                    },
                    {
                        "document_id": "doc_letter",
                        "type": "EMPLOYMENT_CONFIRMATION",
                        "status": "EXTRACTED",
                        "classified_conf": 0.92,
                        "pages": 1,
                    },
                ],
                _ev(1),
                _ev(2),
                _ev(3),
            ),
            "reconciliation.get": _result("reconciliation.get", [], _ev(4)),
            "policy.evaluate": _result(
                "policy.evaluate",
                {
                    "blockers": [],
                    "flags": [],
                    "gates": [
                        {"rule_id": "ELG-02", "result": "PASS"},
                        {"rule_id": "AFF-01", "result": "PASS"},
                    ],
                },
                _ev(5),
            ),
            "affordability.compute": _result(
                "affordability.compute",
                {
                    "dsr": 0.24,
                    "dsr_limit": 0.5,
                    "headroom": 0.26,
                    "instalment": 262.22,
                    "residual": 3210.0,
                    "capacity_score": 84,
                    "calc_id": "calc_S1CAPACITY",
                },
                _ev(6),
            ),
            "risk.score": _result(
                "risk.score",
                {
                    "model_run_id": "mr_S1",
                    "champion": {"pd_12m": 0.0121, "grade": "A", "ood_score": 0.14},
                    "conduct_score": 85,
                    "conduct_calc_id": "calc_S1CONDUCT",
                    "reason_codes": ["CON-01"],
                },
                _ev(7),
            ),
            "fraud.assess": _result(
                "fraud.assess",
                {"findings": [], "integrity_score": 100, "level": "LOW", "calc_id": "calc_S1INTEGRITY"},
                _ev(8),
            ),
            "member.commitment_score": _result(
                "member.commitment_score", {"score": 88, "calc_id": "calc_S1COMMITMENT"}, _ev(9)
            ),
            "member.profile": _result(
                "member.profile",
                {
                    "member_id": "«MEMBER_1»",
                    "tenure_months": 108,
                    "status": "ACTIVE",
                    "branch_id": "BR-01",
                    "employer_sector": "PUBLIC_ADMIN",
                },
                _ev(10),
            ),
            "savings.get": _result(
                "savings.get", {"balance": 7400.0, "slope_180d": 8.4, "paused_months": 0}, _ev(51)
            ),
            "shares.get": _result("shares.get", {"units": 380, "ratio": 0.07, "min_required": 100}, _ev(52)),
            "interactions.get": _result("interactions.get", [], _ev(53)),
            "hardship.get": _result(
                "hardship.get", {"active_arrangement": None, "prior_requests": []}, _ev(54)
            ),
            "history.get": _result(
                "history.get",
                {
                    "ontime_rate_24m": 0.96,
                    "arrears_12m": 0,
                    "months_since_last_arrears": None,
                    "restructures_36m": 0,
                    "facilities": [],
                },
                _ev(55),
            ),
            "bureau.get": _result(
                "bureau.get", {"grade": "A", "adverse_flags": [], "as_of": "2026-08-31"}, _ev(56)
            ),
        },
        expected={"tier": "FAST", "recommendation": "APPROVE", "blockers": [], "fraud_level": "LOW"},
    )


def _income_discrepancy_case() -> GoldenCase:
    """docs/11 S2 — the payslip and the employer's record disagree."""
    case_id = "case_S2INCOME"
    return GoldenCase(
        case_id=case_id,
        title="Income discrepancy",
        scenario="S2",
        snapshot=_snapshot(case_id, amount="25000.00", tenor=48, tenure=120),
        tool_results={
            "documents.list": _result(
                "documents.list",
                [
                    {
                        "document_id": "doc_payslip",
                        "type": "PAYSLIP_LATEST_3",
                        "status": "EXTRACTED",
                        "classified_conf": 0.93,
                        "pages": 1,
                    }
                ],
                _ev(11),
            ),
            "reconciliation.get": _result(
                "reconciliation.get",
                [
                    {
                        "finding_id": "fnd_S2",
                        "code": "INT-03",
                        "severity": "MEDIUM",
                        "document_id": "doc_payslip",
                        "detail": {
                            "payslip_net_median": 2070.0,
                            "deduction_record_median": 2201.15,
                            "variance": 0.0596,
                        },
                    }
                ],
                _ev(12),
            ),
            "policy.evaluate": _result(
                "policy.evaluate",
                {
                    "blockers": [],
                    "flags": ["THIN_HEADROOM"],
                    "gates": [{"rule_id": "AFF-01", "result": "PASS"}],
                },
                _ev(13),
            ),
            "affordability.compute": _result(
                "affordability.compute",
                {
                    "dsr": 0.41,
                    "dsr_limit": 0.5,
                    "headroom": 0.09,
                    "instalment": 651.04,
                    "residual": 1980.0,
                    "capacity_score": 62,
                    "calc_id": "calc_S2CAPACITY",
                },
                _ev(14),
            ),
            "risk.score": _result(
                "risk.score",
                {
                    "model_run_id": "mr_S2",
                    "champion": {"pd_12m": 0.0244, "grade": "B", "ood_score": 0.31},
                    "conduct_score": 79,
                    "conduct_calc_id": "calc_S2CONDUCT",
                    "reason_codes": ["CON-01"],
                },
                _ev(15),
            ),
            "fraud.assess": _result(
                "fraud.assess",
                {
                    "findings": [{"code": "INT-03", "severity": "MEDIUM", "rule": "income_variance"}],
                    "integrity_score": 80,
                    "level": "MEDIUM",
                    "calc_id": "calc_S2INTEGRITY",
                },
                _ev(16),
            ),
        },
        expected={
            "tier": "EXTENDED",
            "recommendation": "APPROVE",
            "fraud_level": "MEDIUM",
            "finding": "INT-03",
        },
    )


def _tampered_case() -> GoldenCase:
    """docs/11 S3 — an edited total and an image seen under another name."""
    case_id = "case_S3TAMPER"
    return GoldenCase(
        case_id=case_id,
        title="Altered document and reused image",
        scenario="S3",
        snapshot=_snapshot(case_id, amount="18000.00", tenor=36, tenure=54),
        tool_results={
            "documents.list": _result(
                "documents.list",
                [
                    {
                        "document_id": "doc_payslip",
                        "type": "PAYSLIP_LATEST_3",
                        "status": "EXTRACTED",
                        "classified_conf": 0.91,
                        "pages": 1,
                    },
                    {
                        "document_id": "doc_bank",
                        "type": "BANK_STATEMENT_3M",
                        "status": "EXTRACTED",
                        "classified_conf": 0.88,
                        "pages": 3,
                    },
                ],
                _ev(21),
                _ev(22),
            ),
            # A fraud scenario is still a case with an income. Without this the
            # gates saw no income at all and failed affordability, which routed
            # S3 on the wrong reason entirely: the case is about an altered
            # document and it was being blocked for being unaffordable.
            "affordability.compute": _result(
                "affordability.compute",
                {
                    # Internally consistent, because the gates recover income
                    # from these figures: income = instalment / dsr, and the
                    # residual has to be what is left after the commitments
                    # that income implies. An inconsistent set makes the
                    # residual rule fail on a case that is comfortably
                    # affordable, which is how S5 came to be blocked by AFF-03
                    # in a scenario about a guarantee ring.
                    "dsr": 0.1849,
                    "dsr_limit": 0.5,
                    "headroom": 0.3151,
                    "instalment": 573.10,
                    "residual": 1826.90,
                    "capacity_score": 76,
                    "calc_id": "calc_S3CAPACITY",
                },
                _ev(7),
            ),
            "forensics.get": _result(
                "forensics.get",
                [
                    {
                        "document_id": "doc_payslip",
                        "code": "INT-01",
                        "severity": "HIGH",
                        "detail": {"check": "payslip_arithmetic", "deviation": 214.6},
                    },
                    {
                        "document_id": "doc_bank",
                        "code": "INT-02",
                        "severity": "HIGH",
                        "detail": {"check": "reused_image", "confirmed": True},
                    },
                ],
                _ev(23),
                _ev(24),
            ),
            "reconciliation.get": _result(
                "reconciliation.get",
                [
                    {
                        "finding_id": "fnd_S3",
                        "code": "INT-01",
                        "severity": "HIGH",
                        "document_id": "doc_payslip",
                    }
                ],
                _ev(25),
            ),
            "fraud.assess": _result(
                "fraud.assess",
                {
                    "findings": [
                        {"code": "INT-01", "severity": "HIGH", "rule": "document_metadata"},
                        {"code": "INT-02", "severity": "HIGH", "rule": "reused_image"},
                    ],
                    "integrity_score": 10,
                    "level": "HIGH",
                    "calc_id": "calc_S3INTEGRITY",
                },
                _ev(26),
            ),
            "policy.evaluate": _result(
                "policy.evaluate",
                {
                    "blockers": [],
                    "flags": [],
                    "gates": [{"rule_id": "RT-01", "result": "ROUTE", "route": "COMPLIANCE_REVIEW"}],
                },
                _ev(27),
            ),
        },
        expected={"recommendation": "COMPLIANCE_REVIEW", "fraud_level": "HIGH", "weighted_score": None},
    )


def _policy_breach_case() -> GoldenCase:
    """docs/11 S4 — affordability fails; there is nothing to weigh."""
    case_id = "case_S4BREACH"
    return GoldenCase(
        case_id=case_id,
        title="Policy breach on affordability",
        scenario="S4",
        snapshot=_snapshot(case_id, amount="42000.00", tenor=24, tenure=84),
        tool_results={
            "documents.list": _result(
                "documents.list",
                [
                    {
                        "document_id": "doc_payslip",
                        "type": "PAYSLIP_LATEST_3",
                        "status": "EXTRACTED",
                        "classified_conf": 0.95,
                        "pages": 1,
                    }
                ],
                _ev(31),
            ),
            "policy.evaluate": _result(
                "policy.evaluate",
                {
                    "blockers": [
                        {
                            "rule_id": "AFF-01",
                            "reason_code": "CAP-02",
                            "on_fail": "POLICY_EXCEPTION_OR_DECLINE",
                        }
                    ],
                    "flags": [],
                    "gates": [{"rule_id": "AFF-01", "result": "FAIL"}],
                },
                _ev(32),
            ),
            "affordability.compute": _result(
                "affordability.compute",
                {
                    "dsr": 0.68,
                    "dsr_limit": 0.6,
                    "headroom": -0.08,
                    "instalment": 1890.0,
                    "residual": 890.0,
                    "capacity_score": 22,
                    "calc_id": "calc_S4CAPACITY",
                },
                _ev(33),
            ),
            "limits.get": _result(
                "limits.get",
                {
                    "exposure_now": 12000.0,
                    "limit": 150000.0,
                    "headroom": 138000.0,
                    "calc_id": "calc_S4LIMITS",
                },
                _ev(34),
            ),
            "risk.score": _result(
                "risk.score",
                {
                    "model_run_id": "mr_S4",
                    "champion": {"pd_12m": 0.0402, "grade": "C", "ood_score": 0.22},
                    "conduct_score": 71,
                    "conduct_calc_id": "calc_S4CONDUCT",
                    "reason_codes": ["CON-01"],
                },
                _ev(35),
            ),
        },
        expected={
            "recommendation": "REVIEW",
            "route": "SENIOR_REVIEW",
            "required_authority": "SENIOR_OFFICER",
            "blocker": "AFF-01",
            "weighted_score": None,
        },
    )


def _guarantor_ring_case() -> GoldenCase:
    """docs/11 S5 — a closed loop of guarantees with concurrent borrowing."""
    case_id = "case_S5RING"
    members = [f"M-0012{index:02d}" for index in range(7)]
    return GoldenCase(
        case_id=case_id,
        title="Guarantor ring",
        scenario="S5",
        snapshot=_snapshot(case_id, amount="9000.00", tenor=36, tenure=66, purpose="DEBT_CONSOLIDATION"),
        tool_results={
            "documents.list": _result(
                "documents.list",
                [
                    {
                        "document_id": "doc_identity",
                        "type": "IDENTITY",
                        "status": "EXTRACTED",
                        "classified_conf": 0.95,
                        "pages": 1,
                    }
                ],
                _ev(41),
            ),
            "fraud.assess": _result(
                "fraud.assess",
                {
                    "findings": [
                        {
                            "code": "INT-05",
                            "severity": "HIGH",
                            "rule": "guarantor_cycle",
                            "members": members,
                            "detail": {"cycle_length": 7, "concurrent_applications": 4, "window_days": 49},
                        }
                    ],
                    "integrity_score": 55,
                    "level": "HIGH",
                    "calc_id": "calc_S5INTEGRITY",
                },
                _ev(42),
            ),
            # Same reason as S3. The ring is the scenario; the member's own
            # affordability is unremarkable and has to be present or the gates
            # block the case for the wrong thing.
            "affordability.compute": _result(
                "affordability.compute",
                {
                    "dsr": 0.0896,
                    "dsr_limit": 0.5,
                    "headroom": 0.4104,
                    "instalment": 286.55,
                    "residual": 2311.45,
                    "capacity_score": 79,
                    "calc_id": "calc_S5CAPACITY",
                },
                _ev(7),
            ),
            "graph.neighbours": _result(
                "graph.neighbours",
                {
                    "nodes": [{"id": f"member:{m}", "kind": "member", "ref": m} for m in members],
                    "edges": [
                        {
                            "source": f"member:{members[i]}",
                            "target": f"member:{members[(i + 1) % 7]}",
                            "kind": "guarantees",
                        }
                        for i in range(7)
                    ],
                    "graph_ref": "ev_S5GRAPH",
                    "depth": 2,
                },
                _ev(43),
            ),
            "policy.evaluate": _result(
                "policy.evaluate",
                {
                    "blockers": [],
                    "flags": [],
                    "gates": [{"rule_id": "RT-01", "result": "ROUTE", "route": "COMPLIANCE_REVIEW"}],
                },
                _ev(44),
            ),
            "risk.score": _result(
                "risk.score",
                {
                    "model_run_id": "mr_S5",
                    "champion": {"pd_12m": 0.0318, "grade": "C", "ood_score": 0.44},
                    "conduct_score": 74,
                    "conduct_calc_id": "calc_S5CONDUCT",
                    "reason_codes": ["CON-01"],
                },
                _ev(45),
            ),
        },
        expected={
            "fraud_level": "HIGH",
            "finding": "INT-05",
            # The route, not the recommendation. `COMPLIANCE_REVIEW` is what
            # the platform recommends and `COMPLIANCE` is where the case goes
            # (docs/11 S3), and the fixture had the recommendation's name in
            # the route's field.
            "route": "COMPLIANCE",
            "cycle_length": 7,
        },
    )


GOLDEN: tuple[GoldenCase, ...] = (
    _clean_case(),
    _income_discrepancy_case(),
    _tampered_case(),
    _policy_breach_case(),
    _guarantor_ring_case(),
)


def write_golden(out: Path = GOLDEN_DIR) -> list[Path]:
    """Write the fixtures to disk, so the harness and the UI can read them."""
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for case in GOLDEN:
        path = out / f"{case.scenario}_{case.case_id}.json"
        path.write_text(json.dumps(case.as_dict(), indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


def load_golden(out: Path = GOLDEN_DIR) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for path in sorted(out.glob("*.json")):
        body = json.loads(path.read_text())
        cases.append(GoldenCase(**body))
    return cases or list(GOLDEN)

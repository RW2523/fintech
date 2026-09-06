"""Planted guarantee rings (docs/10 §7, demo scenario S5).

The generated population produces guarantees at random within an employer, so
short cycles appear on their own. A ring is different: seven members each
guaranteeing the next, closing back to the first, with several applications
inside a couple of months. It is the pattern the fraud service exists to
notice, and it has to be planted deliberately or there is nothing to find.

Planting is a separate step, applied after generation, so the population's
measured counts stay exactly what T-020 recorded and the planted rows are
counted on their own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["PlantedRing", "load_rings", "plant_rings"]

#: docs/10 §7 — seven members, A through G, closing back to A.
RING_SIZE = 7

#: docs/10 §7 — four of them apply inside sixty days.
APPLICATIONS_IN_RING = 4
APPLICATION_WINDOW_DAYS = 60

#: A ring is only visible if its members guarantee each other rather than
#: strangers, so they are drawn from one employer: that is also what makes it
#: plausible, and what makes an honest employer-based cluster the thing the
#: rules have to tell it apart from.
_MIN_EMPLOYER_POOL = 24


@dataclass(frozen=True, slots=True)
class PlantedRing:
    """One ring, and everything a test needs to look for it."""

    ring_id: str
    members: tuple[str, ...]
    employer_id: str
    #: (account_id, guarantor_member_id) pairs forming the cycle.
    guarantees: tuple[tuple[str, str], ...]
    application_ids: tuple[str, ...]
    first_application: str
    last_application: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ring_id": self.ring_id,
            "members": list(self.members),
            "employer_id": self.employer_id,
            "guarantees": [list(pair) for pair in self.guarantees],
            "application_ids": list(self.application_ids),
            "first_application": self.first_application,
            "last_application": self.last_application,
            "expected": {
                "cycle_length": len(self.members),
                "reason_code": "INT-05",
                "severity": "HIGH",
                "why": (
                    f"{APPLICATIONS_IN_RING} applications inside "
                    f"{APPLICATION_WINDOW_DAYS} days within the cycle"
                ),
            },
        }


@dataclass
class PlantResult:
    """What planting added, so it can be counted separately."""

    rings: list[PlantedRing] = field(default_factory=list)
    guarantee_rows: list[dict[str, Any]] = field(default_factory=list)
    application_rows: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rings": [r.as_dict() for r in self.rings],
            "guarantees_added": len(self.guarantee_rows),
            "applications_added": len(self.application_rows),
        }


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _pick_employer(
    rng: np.random.Generator, members: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    by_employer: dict[str, list[dict[str, Any]]] = {}
    for member in members:
        by_employer.setdefault(str(member["employer_id"]), []).append(member)
    usable = sorted(e for e, pool in by_employer.items() if len(pool) >= _MIN_EMPLOYER_POOL)
    if not usable:
        raise ValueError(f"no employer has {_MIN_EMPLOYER_POOL} members; generate a larger population")
    chosen = str(rng.choice(usable))
    return chosen, sorted(by_employer[chosen], key=lambda m: str(m["member_id"]))


def plant_rings(
    out: Path,
    *,
    seed: int = 42,
    count: int = 1,
    as_of: date | None = None,
) -> PlantResult:
    """Add ring guarantees and their applications to a generated population.

    Existing rows are never touched: the ring is additive, so every count
    T-020 measured still holds and the planted rows are reported on their own.
    """
    rng = np.random.default_rng(seed ^ 0x5150)
    members = _read(out / "member.jsonl")
    accounts = _read(out / "account.jsonl")
    if not members or not accounts:
        raise FileNotFoundError(f"no generated population under {out}")

    accounts_by_member: dict[str, list[dict[str, Any]]] = {}
    for account in accounts:
        accounts_by_member.setdefault(str(account["member_id"]), []).append(account)

    existing = {(row["account_id"], row["guarantor_member_id"]) for row in _read(out / "guarantor.jsonl")}
    applications = _read(out / "documents" / "applications.jsonl")
    next_application = 1 + max(
        (int(str(a["application_id"]).rsplit("-", 1)[1]) for a in applications), default=0
    )

    last_open = max(date.fromisoformat(str(a["opened_at"])) for a in accounts)
    anchor = as_of or last_open

    result = PlantResult()
    used: set[str] = set()

    for index in range(count):
        employer_id, pool = _pick_employer(rng, members)
        candidates = [
            m for m in pool if str(m["member_id"]) not in used and accounts_by_member.get(str(m["member_id"]))
        ]
        if len(candidates) < RING_SIZE:
            raise ValueError(f"employer {employer_id} has too few members with accounts")

        picked = [candidates[i] for i in rng.choice(len(candidates), size=RING_SIZE, replace=False)]
        ring_members = tuple(str(m["member_id"]) for m in picked)
        used.update(ring_members)

        # A guarantees B's account, B guarantees C's, ... G guarantees A's.
        guarantees: list[tuple[str, str]] = []
        for position, member_id in enumerate(ring_members):
            borrower = ring_members[(position + 1) % RING_SIZE]
            account = accounts_by_member[borrower][0]
            key = (str(account["account_id"]), member_id)
            if key in existing:
                continue
            existing.add(key)
            guarantees.append(key)
            result.guarantee_rows.append(
                {
                    "account_id": key[0],
                    "guarantor_member_id": member_id,
                    "since": str(account["opened_at"]),
                }
            )

        # Four of the seven apply inside sixty days, which is what turns the
        # cycle from a curiosity into a HIGH finding.
        applicants = ring_members[:APPLICATIONS_IN_RING]
        offsets = sorted(int(v) for v in rng.integers(0, APPLICATION_WINDOW_DAYS, size=APPLICATIONS_IN_RING))
        application_ids: list[str] = []
        for member_id, offset in zip(applicants, offsets, strict=True):
            application_id = f"APP-{next_application:05d}"
            next_application += 1
            application_ids.append(application_id)
            result.application_rows.append(
                {
                    "application_id": application_id,
                    "member_id": member_id,
                    "product_code": "PF-STD",
                    "purpose": "DEBT_CONSOLIDATION",
                    "amount": f"{float(rng.uniform(4000, 12000)):.2f}",
                    "tenor_months": int(rng.integers(24, 49)),
                    "created_at": (anchor - timedelta(days=APPLICATION_WINDOW_DAYS - offset)).isoformat(),
                    "planted": "GUARANTOR_RING",
                }
            )

        dates = sorted(
            str(row["created_at"])
            for row in result.application_rows
            if row["application_id"] in application_ids
        )
        result.rings.append(
            PlantedRing(
                ring_id=f"RING-{index + 1:02d}",
                members=ring_members,
                employer_id=employer_id,
                guarantees=tuple(guarantees),
                application_ids=tuple(application_ids),
                first_application=dates[0],
                last_application=dates[-1],
            )
        )

    _append(out / "guarantor.jsonl", result.guarantee_rows)
    _append(out / "documents" / "applications.jsonl", result.application_rows)
    (out / "rings.json").write_text(json.dumps(result.as_dict(), indent=2) + "\n")
    return result


def _append(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def load_rings(out: Path) -> list[dict[str, Any]]:
    """The planted rings, for tests and the evaluation harness."""
    path = out / "rings.json"
    if not path.is_file():
        return []
    return list(json.loads(path.read_text())["rings"])

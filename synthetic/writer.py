"""Writing a generated population to disk, reproducibly.

Rows go out as JSON Lines with sorted keys in generation order, and a manifest
records a digest per table. Two runs with the same seed produce byte-identical
files, which is what makes the golden cases and the demo repeatable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cio_common.hashing import sha256

__all__ = ["DEFAULT_OUT", "Manifest", "read_table", "write_population"]

DEFAULT_OUT = Path("synthetic/out")


@dataclass(frozen=True, slots=True)
class Manifest:
    seed: int
    members: int
    months: int
    generated_at: str
    tables: dict[str, dict[str, Any]]

    @property
    def digest(self) -> str:
        """One digest over every table, for a quick equality check."""
        return sha256("".join(f"{name}:{body['sha256']}" for name, body in sorted(self.tables.items())))


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """Drop the generator's own bookkeeping fields."""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _write_table(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    body = "".join(
        json.dumps(_public(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for row in rows
    )
    path.write_text(body, encoding="utf-8")
    return {"rows": len(rows), "bytes": len(body.encode("utf-8")), "sha256": sha256(body)}


def write_population(population: Any, out: Path = DEFAULT_OUT, *, seed: int, months: int) -> Manifest:
    """Write every table plus the member profiles, and return the manifest."""
    out.mkdir(parents=True, exist_ok=True)
    tables: dict[str, dict[str, Any]] = {}

    for name, rows in population.tables().items():
        tables[name] = _write_table(out / f"{name}.jsonl", rows)

    profiles = [{"member_id": k, **v} for k, v in sorted(population.profiles.items())]
    tables["profile"] = _write_table(out / "profile.jsonl", profiles)

    # Outcome labels are derived from the payment history, never from the
    # archetype that produced it (docs/10 §5).
    from synthetic.labels import label_accounts

    outcomes = label_accounts(population.schedules, population.payments, population.arrangements)
    tables["outcome"] = _write_table(
        out / "outcome.jsonl", [month.as_row() for outcome in outcomes for month in outcome.months]
    )

    manifest = Manifest(
        seed=seed,
        members=len(population.members),
        months=months,
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        tables=tables,
    )
    # generated_at is excluded from the digest on purpose: it is the one field
    # that legitimately differs between two otherwise identical runs
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "seed": manifest.seed,
                "members": manifest.members,
                "months": manifest.months,
                "generated_at": manifest.generated_at,
                "digest": manifest.digest,
                "tables": manifest.tables,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def read_table(name: str, out: Path = DEFAULT_OUT) -> list[dict[str, Any]]:
    path = out / f"{name}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

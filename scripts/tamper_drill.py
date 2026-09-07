"""The tamper drill: prove the chains detect an alteration (docs/14 §4).

    uv run python scripts/tamper_drill.py

Verifies both chains green, alters one row in each the way somebody with the
database password would have to, verifies red, then puts the row back and
verifies green again. Nothing is left changed.

A tamper-evident store that is never tested is a claim, not a control.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

# Run as `python scripts/x.py`, so the repository root is not on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.signin import token_for

BASE = "http://localhost:8000"
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    "docker/.env",
    "-f",
    "docker/compose.yaml",
    "exec",
    "-T",
    "postgres",
    "psql",
    "-U",
    os.environ.get("POSTGRES_USER", "cio"),
    "-d",
    os.environ.get("POSTGRES_DB", "cio"),
    "-tAc",
]


def sql(statement: str) -> str:
    """Run one statement as the database owner.

    Through psql rather than the services, because the point is to do what an
    insider with the password can do, which is exactly what the services will
    not let anybody do through an API. The statements below are built by string
    interpolation and marked as such: that is what makes this a tamper drill
    rather than an ordinary write, and there is no untrusted input anywhere in
    it.
    """
    result = subprocess.run(  # noqa: S603 - a fixed argv; the statement is this file's own
        [*COMPOSE, statement], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit(f"  psql refused: {result.stderr.strip()}")
    return result.stdout.strip()


async def verify(client: httpx.AsyncClient, name: str, path: str) -> dict[str, Any]:
    body = (await client.get(f"{BASE}{path}")).json()
    verdict = body.get("verified")
    if verdict is None:
        verdict = body.get("intact")
    print(
        f"  {name:<8} {'GREEN' if verdict else 'RED  '} "
        f"({body.get('entries_checked', 0)} entries, {len(body.get('breaks') or [])} breaks)"
    )
    return {"verified": bool(verdict), "breaks": body.get("breaks") or []}


async def main() -> int:
    ok = True
    async with httpx.AsyncClient(timeout=60.0) as anon:
        token = await token_for(anon, "head_of_risk", base=BASE)

    async with httpx.AsyncClient(timeout=60.0, headers={"authorization": f"Bearer {token}"}) as c:
        print("before:")
        ok &= (await verify(c, "ledger", "/api/decision/ledger/verify"))["verified"]
        ok &= (await verify(c, "audit", "/api/audit/audit/verify"))["verified"]

        # --- the ledger ---------------------------------------------------
        original = sql("SELECT payload::text FROM ledger.entry ORDER BY seq LIMIT 1")
        if not original:
            print("  nothing in the ledger to tamper with; run the seeder first")
            return 1
            # Flipped, not set: writing the value that is already there alters
        # nothing, and the drill would then report a clean chain as proof.
        body = json.loads(original)
        body["recommendation"] = "DECLINE" if body.get("recommendation") != "DECLINE" else "APPROVE"
        altered = json.dumps(body)
        sql("ALTER TABLE ledger.entry DISABLE TRIGGER ledger_no_update")
        overwrite = (
            f"UPDATE ledger.entry SET payload = {_quote(altered)}::jsonb "  # noqa: S608
            "WHERE seq = (SELECT min(seq) FROM ledger.entry)"
        )
        sql(overwrite)

        # --- the audit trail ------------------------------------------------
        audit_original = sql("SELECT action FROM audit.entry ORDER BY seq LIMIT 1")
        if audit_original:
            sql("ALTER TABLE audit.entry DISABLE TRIGGER audit_no_mutation")
            sql(
                "UPDATE audit.entry SET action = 'decision.declined' "
                "WHERE seq = (SELECT min(seq) FROM audit.entry)"
            )

        print("\nafter altering one row in each:")
        ledger_after = await verify(c, "ledger", "/api/decision/ledger/verify")
        ok &= not ledger_after["verified"]
        if audit_original:
            audit_after = await verify(c, "audit", "/api/audit/audit/verify")
            ok &= not audit_after["verified"]

        # --- put it back ----------------------------------------------------
        restore = (
            f"UPDATE ledger.entry SET payload = {_quote(original)}::jsonb "  # noqa: S608
            "WHERE seq = (SELECT min(seq) FROM ledger.entry)"
        )
        sql(restore)
        sql("ALTER TABLE ledger.entry ENABLE TRIGGER ledger_no_update")
        if audit_original:
            restore_audit = (
                f"UPDATE audit.entry SET action = {_quote(audit_original)} "  # noqa: S608
                "WHERE seq = (SELECT min(seq) FROM audit.entry)"
            )
            sql(restore_audit)
            sql("ALTER TABLE audit.entry ENABLE TRIGGER audit_no_mutation")

        print("\nafter putting it back:")
        ok &= (await verify(c, "ledger", "/api/decision/ledger/verify"))["verified"]
        if audit_original:
            ok &= (await verify(c, "audit", "/api/audit/audit/verify"))["verified"]

    print("\n  tamper drill:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


sys.exit(asyncio.run(main()))

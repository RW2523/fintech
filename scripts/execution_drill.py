"""The execution drill: an approval becomes a facility (docs/08 §7).

    uv run python scripts/execution_drill.py

Runs the whole write path against the stack. A decision is recorded and a
token issued for it, the action is proposed and carried out, the core opens the
account, and the same execute is replayed to prove it is a no-op. Then the same
token is presented on a second action, which must be refused.

It is the T-052 acceptance, kept runnable: a write path that is exercised only
once is a write path nobody should trust with money.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cio_common.ids import new_id

BASE = "http://localhost:8000"
PRODUCT = "PF-STD"
SNAPSHOT = "snap_0000000000000000000T052AC"
CASE = "case_T052ACCEPT"

#: Fresh every run. Reusing an action id would have the second run find the
#: first run's executed action and take the replay path, so every check after
#: it would pass without running. Minted with the platform's own id function
#: rather than a timestamp, which two runs in the same second would share.
ACTION_ID = new_id("act")
SECOND_ACTION_ID = new_id("act")

RECORD = {
    "product_code": PRODUCT,
    "snapshot_id": SNAPSHOT,
    "case_type": "ORIGINATION",
    "tier": "FAST",
    "requested_amount": "8000",
    "policy_result": {
        "blockers": [],
        "flags": [],
        "rules": [],
        "evidence_coverage": 1.0,
        "required_authority": "CREDIT_OFFICER",
        "policy_version": "policy/PF-STD/2026.09.1",
    },
    "factor_scores": [
        {"family": "CAPACITY", "score": 92, "calc_id": "calc_a"},
        {"family": "CONDUCT", "score": 90, "calc_id": "calc_b"},
        {"family": "INTEGRITY", "score": 100, "calc_id": "calc_e", "level": "NONE"},
    ],
    "opinions": [],
    "model_health": "GREEN",
}


async def member_id(client: httpx.AsyncClient) -> str:
    """A real member: the core refuses to open an account for a stranger, and
    a drill that used a made-up id would never reach the write it is testing.

    The core stub reads one member at a time, so the id is taken from the
    generated population rather than listed.
    """
    for candidate in (f"M-{n:06d}" for n in range(1, 40)):
        response = await client.get(f"{BASE}/api/core_stub/core/members/{candidate}")
        if response.status_code == 200:
            return candidate
    raise SystemExit("  no member found; run the synthetic loader first")


async def main() -> int:
    ok = True
    async with httpx.AsyncClient(timeout=60.0) as anon:
        token = (await anon.post(f"{BASE}/api/auth/dev-token", json={"role": "system"})).json()[
            "access_token"
        ]

    async with httpx.AsyncClient(timeout=60.0, headers={"authorization": f"Bearer {token}"}) as c:
        member = await member_id(c)
        print(f"  member {member}")

        record = (await c.post(f"{BASE}/api/policy/policy/synthesize", json=RECORD)).json()
        appended = (
            await c.post(
                f"{BASE}/api/decision/recommendations",
                json={"decision_record": record, "case_id": CASE, "member_id": member},
            )
        ).json()
        print(f"  decision {record['recommendation']} recorded at {appended['entry_id']}")

        issued = (
            await c.post(
                f"{BASE}/api/decision/tokens",
                json={
                    "action_id": ACTION_ID,
                    "decision_record_id": record["decision_record_id"],
                    "case_id": CASE,
                    "member_id": member,
                    "product_code": PRODUCT,
                    "max_amount": "8000",
                    "idempotency_key": f"t052-{ACTION_ID}",
                },
            )
        ).json()
        token_id = issued["token_id"]
        print(f"  token {token_id}")

        action_id = ACTION_ID
        proposal = {
            "action_id": action_id,
            "case_id": CASE,
            "member_id": member,
            "decision_record_id": record["decision_record_id"],
            "level": "L3",
            "type": "APPROVE_FINANCING",
            "parameters": {
                "product_code": PRODUCT,
                "amount": "8000.00",
                "tenor_months": 36,
                "instalment": "262.22",
                "profit_rate": "0.09",
                "due_day": 1,
            },
            "rationale": {"text": "approved on the decision record", "evidence_refs": []},
            "requires": "OFFICER",
            "proposed_by": "policy",
        }
        proposed = await c.post(f"{BASE}/api/execution/action-proposals", json=proposal)
        print(f"  proposal -> {proposed.status_code} {proposed.json().get('state')}")
        ok &= proposed.status_code == 200

        executed = await c.post(
            f"{BASE}/api/execution/actions/{action_id}/execute",
            json={"token_id": token_id, "product_code": PRODUCT},
        )
        body: dict[str, Any] = executed.json()
        print(f"  execute -> {executed.status_code} {body.get('state')} refs {body.get('core_refs')}")
        ok &= executed.status_code == 200 and body.get("state") == "EXECUTED"

        again = await c.post(
            f"{BASE}/api/execution/actions/{action_id}/execute",
            json={"token_id": token_id, "product_code": PRODUCT},
        )
        print(f"  replay  -> {again.status_code} replayed={again.json().get('replayed')}")
        ok &= again.status_code == 200 and again.json().get("replayed") is True

        # The same token on a different action must be refused: it is spent.
        second = dict(proposal, action_id=SECOND_ACTION_ID)
        await c.post(f"{BASE}/api/execution/action-proposals", json=second)
        reused = await c.post(
            f"{BASE}/api/execution/actions/{second['action_id']}/execute",
            json={"token_id": token_id, "product_code": PRODUCT},
        )
        print(f"  reuse   -> {reused.status_code} {reused.json().get('error', {}).get('code')}")
        ok &= reused.status_code == 403

    print("\n  T-052 acceptance:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))

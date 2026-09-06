"""The override drill: a person departs from the recommendation (docs/03 §7).

    uv run python scripts/override_drill.py

An officer decides a case the way the recommendation said, and a second officer
overrides one. Then it checks that the authority rule holds against the API
whatever a screen allowed, that an override without a reason is refused, and
that both decisions appear in the governance series.

It is the T-054 acceptance. An override rate nobody measures is a control
nobody has.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import httpx

BASE = "http://localhost:8000"


async def token_for(client: httpx.AsyncClient, role: str) -> str:
    minted = await client.post(f"{BASE}/api/auth/dev-token", json={"role": role})
    minted.raise_for_status()
    return str(minted.json()["access_token"])


async def queued(client: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
    response = await client.get(f"{BASE}/api/decision/queue", headers={"authorization": f"Bearer {token}"})
    response.raise_for_status()
    return list(response.json()["decisions"])


async def decide(
    client: httpx.AsyncClient, token: str, role: str, entry: dict[str, Any], **body: Any
) -> httpx.Response:
    return await client.post(
        f"{BASE}/api/decision/human-decisions",
        headers={"authorization": f"Bearer {token}"},
        json={
            "decision_record_id": entry["decision_record_id"],
            "case_id": entry["case_id"],
            "actor_id": f"{role}-drill",
            "role": role,
            **body,
        },
    )


async def main() -> int:
    ok = True
    async with httpx.AsyncClient(timeout=60.0) as c:
        officer = await token_for(c, "officer")
        senior = await token_for(c, "senior_officer")
        compliance = await token_for(c, "compliance")

        rows = await queued(c, officer)
        undecided = [r for r in rows if not r["decided"] and r["case_id"]]
        # The first case an officer may actually decide, not merely the first
        # in the queue: picking one that needs a senior would make step 1 fail
        # for the reason step 2 is about.
        within = [r for r in undecided if r["required_authority"] == "CREDIT_OFFICER"]
        if not within or len(undecided) < 2:
            print("  not enough undecided cases; run scripts/seed_demo_case.py first")
            return 1

        agreeing = within[0]
        departing = next(r for r in undecided if r["decision_record_id"] != agreeing["decision_record_id"])

        # --- 1. an action within authority is recorded ----------------------
        recommended = "APPROVE" if agreeing["recommendation"] == "APPROVE" else "DECLINE"
        response = await decide(c, officer, "officer", agreeing, final_action=recommended, override=False)
        print(f"  agreeing   -> {response.status_code} {response.json().get('final_action')}")
        ok &= response.status_code == 200

        # --- 2. authority is checked by the API, not by the screen ----------
        needs_more = next((r for r in undecided if r["required_authority"] != "CREDIT_OFFICER"), None)
        if needs_more:
            refused = await decide(c, officer, "officer", needs_more, final_action="APPROVE", override=False)
            print(f"  over limit -> {refused.status_code} {refused.json().get('error', {}).get('code')}")
            ok &= refused.status_code == 403
        else:
            print("  over limit -> no case needs more than an officer; not exercised")

        # --- 3. an override without a reason is refused ---------------------
        against = "DECLINE" if departing["recommendation"] != "DECLINE" else "APPROVE"
        naked = await decide(c, senior, "senior_officer", departing, final_action=against, override=True)
        print(f"  no reason  -> {naked.status_code}")
        ok &= naked.status_code == 422

        # --- 4. an override with one is recorded ----------------------------
        overridden = await decide(
            c,
            senior,
            "senior_officer",
            departing,
            final_action=against,
            override=True,
            override_reason={
                "code": "OVR-05",
                "text": "the member has banked here for nine years and the score misses it",
            },
        )
        print(f"  override   -> {overridden.status_code} {overridden.json().get('final_action')}")
        ok &= overridden.status_code == 200

        # --- 5. it reaches the governance series ----------------------------
        series = await c.get(
            f"{BASE}/api/governance/governance/overrides",
            headers={"authorization": f"Bearer {compliance}"},
        )
        body = series.json()
        print(
            f"  series     -> {body['decisions']} decisions, {body['overrides']} overrides, "
            f"rate {body['override_rate']}, OVR-05 {body['by_reason'].get('OVR-05')}"
        )
        ok &= body["overrides"] >= 1
        ok &= body["by_reason"].get("OVR-05", 0) >= 1

        attention = await c.get(
            f"{BASE}/api/governance/governance/attention",
            headers={"authorization": f"Bearer {compliance}"},
        )
        reasons = {row["why"] for row in attention.json()["cases"]}
        print(f"  attention  -> {sorted(reasons)}")
        ok &= "OVERRIDE" in reasons

    print("\n  T-054 acceptance:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))

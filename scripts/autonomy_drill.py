"""The autonomy drill: turn the dial up, act alone, then stop (docs/05 §6).

    uv run python scripts/autonomy_drill.py

Exercises the whole loop against the running stack. Two heads move the dial to
AUTONOMOUS_WITHIN_LIMITS; a clean small case decides itself, is recorded with a
token, and lands in the sampling queue where a senior officer reviews it; a head
of risk pulls the kill switch and the same case routes to a person with reason
KILL_SWITCH; releasing the switch restores the setting the institution chose.
The dial is returned to ASSIST at the end.

Nothing here is asserted against a fixture: every check reads what the services
actually returned. It is the T-051 acceptance, kept runnable because a stop
control that is never exercised is a stop control nobody should trust.
"""

from __future__ import annotations

import asyncio
import sys

import httpx

BASE = "http://localhost:8000"
PRODUCT = "PF-STD"
HEADS = [
    {"role": "HEAD_OF_CREDIT", "actor_id": "u-credit"},
    {"role": "HEAD_OF_RISK", "actor_id": "u-risk"},
]

CASE = {
    "product_code": PRODUCT,
    # Chosen so the 10% sampling draw selects it. The draw is deterministic on
    # the snapshot id, so the acceptance exercises the queue rather than
    # hoping to.
    "snapshot_id": "snap_0000000000000000000T051100",
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
        {"family": "CAPACITY", "score": 92, "calc_id": "calc_a", "level": None},
        {"family": "CONDUCT", "score": 90, "calc_id": "calc_b"},
        {"family": "COMMITMENT", "score": 95, "calc_id": "calc_c"},
        {"family": "CONDITIONS", "score": 90, "calc_id": "calc_d"},
        {"family": "INTEGRITY", "score": 100, "calc_id": "calc_e", "level": "NONE"},
    ],
    "opinions": [
        {
            "opinion_id": f"op_0000000000000000000000000{i}",
            "agent_id": a,
            "stance": "SUPPORT",
            "confidence": 0.96,
            "unresolved": [],
        }
        for i, a in enumerate(
            [
                "document_evidence",
                "policy_affordability",
                "credit_risk",
                "fraud_integrity",
                "member_relationship",
            ]
        )
    ],
    "model_health": "GREEN",
}


async def main() -> int:
    ok = True
    async with httpx.AsyncClient(timeout=60.0) as anon:
        token = (await anon.post(f"{BASE}/api/auth/dev-token", json={"role": "system"})).json()[
            "access_token"
        ]

    async with httpx.AsyncClient(timeout=60.0, headers={"authorization": f"Bearer {token}"}) as c:
        # --- clean up anything a previous run left -------------------------
        await c.request(
            "DELETE",
            f"{BASE}/api/policy/kill-switch/{PRODUCT}",
            json={"actor_id": "u-risk", "actor_role": "HEAD_OF_RISK", "reason": "reset"},
        )

        # --- 1. turn the dial up -------------------------------------------
        moved = await c.post(
            f"{BASE}/api/policy/autonomy/{PRODUCT}",
            json={"setting": "AUTONOMOUS_WITHIN_LIMITS", "approvers": HEADS, "reason": "T-051 acceptance"},
        )
        print(f"  dial -> {moved.status_code} {moved.json().get('setting')}")
        ok &= moved.status_code == 200

        # --- 2. a clean small case decides itself --------------------------
        record = (await c.post(f"{BASE}/api/policy/policy/synthesize", json=CASE)).json()
        print(
            f"  route {record['route']}  reasons {record['route_reasons']}  sampled {record.get('sampled')}"
        )
        ok &= record["route"] == "AUTONOMOUS"

        appended = (
            await c.post(
                f"{BASE}/api/decision/recommendations",
                json={
                    "decision_record": record,
                    "case_id": "case_T051ACCEPT",
                    "sampling": {"reviewer_role": "SENIOR_OFFICER", "sla_hours": 24},
                },
            )
        ).json()
        print(f"  ledger {appended['entry_id']}  sample {appended.get('sample_id')}")

        # --- 3. a token is issued for it -----------------------------------
        issued = await c.post(
            f"{BASE}/api/decision/tokens",
            json={
                "action_id": "act_0000000000000000000T051AC",
                "decision_record_id": record["decision_record_id"],
                "case_id": "case_T051ACCEPT",
                "member_id": "M-000042",
                "product_code": PRODUCT,
                "max_amount": "8000",
                "idempotency_key": "t051-acceptance",
            },
        )
        print(f"  token -> {issued.status_code} {issued.json().get('token_id')}")
        ok &= issued.status_code in (200, 201)

        # --- 4. it is in the sampling queue --------------------------------
        assert record.get("sampled"), "the acceptance snapshot must be one the draw selects"
        queue = (await c.get(f"{BASE}/api/decision/samples")).json()
        entry = next((s for s in queue["samples"] if s["sample_id"] == appended.get("sample_id")), None)
        print(
            f"  sampling queue: {entry['assigned_role'] if entry else 'ABSENT'} "
            f"due {entry['due_at'] if entry else '-'}"
        )
        ok &= entry is not None

        # As the senior officer the sample was assigned to, not as the system.
        senior = (await c.post(f"{BASE}/api/auth/dev-token", json={"role": "senior_officer"})).json()[
            "access_token"
        ]
        reviewed = await c.post(
            f"{BASE}/api/decision/samples/{appended['sample_id']}/review",
            json={
                "reviewer_id": "u-senior",
                "verdict": "AGREE",
                "notes": "affordability and conduct both check out",
            },
            headers={"authorization": f"Bearer {senior}"},
        )
        print(f"  review -> {reviewed.status_code} chained at {reviewed.json().get('entry_id')}")
        ok &= reviewed.status_code == 200

        # --- 5. the kill switch --------------------------------------------
        stopped = await c.post(
            f"{BASE}/api/policy/kill-switch/{PRODUCT}",
            json={"actor_id": "u-risk", "actor_role": "HEAD_OF_RISK", "reason": "acceptance drill"},
        )
        print(f"  kill switch -> {stopped.status_code} effective {stopped.json().get('effective_setting')}")
        ok &= stopped.status_code == 200

        under_stop = (await c.post(f"{BASE}/api/policy/policy/synthesize", json=CASE)).json()
        print(f"  route {under_stop['route']}  reasons {under_stop['route_reasons']}")
        ok &= under_stop["route"] == "OFFICER_REVIEW"
        ok &= "KILL_SWITCH" in under_stop["route_reasons"]

        # --- 6. releasing restores what was chosen -------------------------
        released = await c.request(
            "DELETE",
            f"{BASE}/api/policy/kill-switch/{PRODUCT}",
            json={"actor_id": "u-risk", "actor_role": "HEAD_OF_RISK", "reason": "drill over"},
        )
        print(f"  released -> effective {released.json().get('effective_setting')}")
        ok &= released.json().get("effective_setting") == "AUTONOMOUS_WITHIN_LIMITS"

        # --- leave the dial where the demo expects it ----------------------
        await c.post(
            f"{BASE}/api/policy/autonomy/{PRODUCT}",
            json={"setting": "ASSIST", "approvers": HEADS, "reason": "restore after acceptance"},
        )

    print("\n  T-051 acceptance:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))

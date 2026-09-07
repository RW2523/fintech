"""Seed decided cases so the workbench has something real to show.

    uv run python scripts/seed_demo_case.py

Runs the golden cases through the real deterministic path. For each one it
uploads a rendered document set from the synthetic corpus, has the document
service classify and extract it, evaluates every hard gate, synthesizes a
DecisionRecord and appends that record to the hash-chained ledger. Nothing is
written straight into a table, and every page image an officer sees is a file
the extractor actually read.

By default the committee is convened too, so every seeded case carries real
agent opinions with real evidence behind them — which is what the workbench's
council panel shows. Pass `--no-council` to skip it: the deliberation is the
slow part, and somebody working on a screen that does not show opinions should
not wait for six agents to argue.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.signin import token_for  # noqa: E402
from synthetic.golden import GOLDEN  # noqa: E402

#: Which tool's number becomes which Decision Factor, matching what the agents
#: report (docs/06 §5). Taking the scores from the golden tool results rather
#: than inventing them here keeps a seeded case consistent with a real run of
#: the same case.
FACTOR_SOURCES: dict[str, tuple[str, str, str]] = {
    "CAPACITY": ("affordability.compute", "capacity_score", "calc_id"),
    "CONDUCT": ("risk.score", "conduct_score", "conduct_calc_id"),
    "INTEGRITY": ("fraud.assess", "integrity_score", "calc_id"),
    "COMMITMENT": ("member.commitment_score", "score", "calc_id"),
}

#: Where the rendered corpus lands (docs/10 §5). Absent is not fatal: the
#: decision still seeds, and the evidence panel then has documents to name but
#: no page image to show.
CORPUS = ROOT / "synthetic" / "out" / "documents"

#: The fixtures carry no date of birth, because no gate in PF-STD reads an age
#: beyond the eligibility floor. One value is used for every seeded case so it
#: is obvious the number is a constant and not a fact about a member.
DEMO_AGE = 41


def _tool(case: Any, name: str) -> Any:
    result = case.tool_results.get(name)
    return result["result"] if result else None


class IncompleteCaseError(ValueError):
    """A fixture that cannot be evaluated, said rather than filled in."""


def case_inputs(case: Any) -> dict[str, Any]:
    """Assemble the flat facts the gates read.

    The snapshot is authoritative for what a member is and what is on the file:
    tenure and documents are frozen at submission and every fixture carries
    them. The tool results supply only what was computed from them.

    It used to read both from the tool results, with `.get(..., 0)` behind
    each. Four of the five fixtures hold a partial, per-agent view -- the fraud
    agent's case carries no member profile, the affordability agent's carries
    one document -- so tenure came out as 0 and the document set as incomplete,
    and S2, S4 and S5 failed eligibility gates that no scenario intended. They
    have been routing to COMPLIANCE in the demo for fabricated reasons since
    they were seeded. A missing value is not a value.
    """
    snapshot = case.snapshot
    affordability = _tool(case, "affordability.compute") or {}
    fraud = _tool(case, "fraud.assess") or {}
    history = _tool(case, "history.get") or {}
    bureau = _tool(case, "bureau.get") or {}
    profile = _tool(case, "member.profile") or {}

    documents = snapshot.get("documents") or []
    if not documents:
        raise IncompleteCaseError(f"{case.case_id} carries no documents; the gates cannot be run")
    confidence = {d["type"]: float(d["confidence"]) for d in documents}
    present = sorted(confidence)

    tenure = snapshot.get("member", {}).get("tenure_months", profile.get("tenure_months"))
    if tenure is None:
        raise IncompleteCaseError(f"{case.case_id} carries no tenure; ELG-02 cannot be evaluated")

    # The instalment and residual are the affordability tool's own numbers, so
    # monthly income is recovered from them rather than guessed: the gate must
    # see the same figures the CAPACITY score was computed from. A fixture with
    # no affordability result has no income to recover, and a zero there is an
    # unaffordable case rather than an unknown one.
    if affordability:
        instalment = float(affordability.get("instalment", 0.0))
        dsr = float(affordability.get("dsr", 0.0))
        income = round(instalment / dsr, 2) if dsr else 0.0
        commitments = round(income - instalment - float(affordability.get("residual", 0.0)), 2)
    else:
        income, commitments = 0.0, 0.0

    required = {"IDENTITY", "PAYSLIP_LATEST_3", "EMPLOYMENT_CONFIRMATION"}

    return {
        "member_status": profile.get("status", "ACTIVE"),
        "member_tenure_months": int(tenure),
        "member_age": DEMO_AGE,
        "member_total_exposure": "0",
        "member_grade": bureau.get("grade", "C"),
        "identity_verified": "IDENTITY" in present,
        "requested_amount": str(snapshot["amount"]),
        "requested_tenor": int(snapshot["tenor_months"]),
        "requested_purpose": snapshot["purpose"],
        "documents_required_complete": required.issubset(present),
        "documents_min_critical_confidence": min(confidence.values()) if confidence else 0.0,
        "documents_present": present,
        "document_confidence": confidence,
        "income_verified": bool(income),
        "income_verified_monthly": str(income),
        "commitments_monthly": str(max(commitments, 0.0)),
        "fraud_level": fraud.get("level", "NONE"),
        "fraud_integrity_score": int(fraud.get("integrity_score", 100)),
        "history_arrears_12m": int(history.get("arrears_12m", 0)),
        "history_late_12m": int(history.get("arrears_12m", 0)),
        "history_restructures": int(history.get("restructures_36m", 0)),
    }


def factor_scores(case: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for family, (tool, field, calc_field) in FACTOR_SOURCES.items():
        result = case.tool_results.get(tool)
        if result is None or field not in result["result"]:
            continue
        payload = result["result"]
        out.append(
            {
                "family": family,
                "score": int(payload[field]),
                "calc_id": payload.get(calc_field, f"calc_{family}"),
                "tool": tool,
                "inputs_digest": "0" * 64,
                "evidence_refs": [r["evidence_id"] for r in result["evidence_refs"]],
            }
        )
    return out


def corpus_applications() -> dict[str, list[dict[str, Any]]]:
    """The rendered corpus, grouped by the application it belongs to."""
    path = CORPUS / "documents.jsonl"
    if not path.is_file():
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        grouped.setdefault(row["application_id"], []).append(row)
    return grouped


def tampered_applications() -> set[str]:
    """Applications the generator planted an anomaly in (docs/10 §6)."""
    path = CORPUS / "anomalies.json"
    if not path.is_file():
        return set()
    return {row["application_id"] for row in json.loads(path.read_text())}


def choose_documents(
    case: Any, corpus: dict[str, list[dict[str, Any]]], tampered: set[str]
) -> list[dict[str, Any]]:
    """One corpus application per scenario, matched to what the case claims.

    A scenario about an altered document is given an application the generator
    actually tampered with, so the forensics have something to find. Everything
    else is given a clean one. The choice is by sorted order rather than random
    so two runs of this script seed the same case with the same paper.
    """
    wants_tamper = "TAMPER" in case.case_id.upper()
    for application in sorted(corpus):
        documents = corpus[application]
        if len(documents) < 3:
            continue
        if (application in tampered) != wants_tamper:
            continue
        return documents
    return []


async def load_documents(
    client: httpx.AsyncClient, base: str, case: Any, documents: list[dict[str, Any]]
) -> int:
    """Register, upload and process each document against the real service."""
    loaded = 0
    for row in documents:
        path = CORPUS / "files" / row["filename"]
        if not path.is_file():
            continue
        data = path.read_bytes()
        mime = mimetypes.guess_type(row["filename"])[0] or "application/octet-stream"

        registered = await client.post(
            f"{base}/api/document/cases/{case.case_id}/documents",
            json={
                "filename": row["filename"],
                "mime": mime,
                "size": len(data),
                "member_id": row["member_id"],
                # What the member says it is. The classifier decides what it
                # is, so this is passed through and never trusted.
                "declared_type": row["type"],
            },
        )
        if registered.status_code not in (200, 201):
            print(
                f"    {row['document_id']}: register refused {registered.status_code} {registered.text[:160]}"
            )
            continue
        document_id = registered.json()["document_id"]

        uploaded = await client.post(
            f"{base}/api/document/documents/{document_id}/upload",
            files={"file": (row["filename"], data, mime)},
        )
        if uploaded.status_code not in (200, 201):
            print(f"    {document_id}: upload refused {uploaded.status_code} {uploaded.text[:160]}")
            continue

        completed = await client.post(
            f"{base}/api/document/documents/{document_id}/complete",
            json={"sha256": row["sha256"]},
        )
        if completed.status_code not in (200, 201):
            print(f"    {document_id}: extraction refused {completed.status_code} {completed.text[:160]}")
            continue
        loaded += 1
    return loaded


def agent_tool_results(case: Any) -> dict[str, list[dict[str, Any]]]:
    """The tool results each agent in the council is entitled to.

    Read from each agent's own bundle rather than listed here, so an agent that
    gains or loses a grant is fed correctly without this script being edited.
    """
    from ai.agents.bundle import load_bundle

    found: dict[str, list[dict[str, Any]]] = {}
    for directory in sorted((ROOT / "ai" / "agents").iterdir()):
        if not (directory / "tools.yaml").is_file():
            continue
        try:
            bundle = load_bundle(directory.name)
        except (OSError, ValueError, KeyError) as exc:
            # A bundle that will not load has no grants to read. That is a
            # problem for that agent, not for this run — but an agent silently
            # missing from the council is worse than a noisy one.
            print(f"  {directory.name}: bundle would not load ({exc}); no tools fed")
            continue
        granted = [grant.name for grant in bundle.tools]
        results = case.results_for(granted)
        if results:
            found[directory.name] = results
    return found


async def convene(
    client: httpx.AsyncClient,
    base: str,
    case: Any,
    policy_result: dict[str, Any],
    tier: str,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Run the council over this case and wait for it to finish.

    The agents get the golden tool results — the same numbers the deterministic
    path scored — because an agent arguing from different figures than the ones
    the record carries is an agent arguing about a different case.
    """
    started = await client.post(
        f"{base}/api/committee/committee/runs",
        json={
            "snapshot": case.snapshot,
            "policy_result": policy_result,
            # What each agent is allowed to have read, from its own bundle.
            # The same numbers the deterministic path scored: an agent arguing
            # from different figures than the record carries is an agent
            # arguing about a different case.
            "tool_results": agent_tool_results(case),
            "case_type": "ORIGINATION",
            "tier": tier,
        },
        timeout=60.0,
    )
    if started.status_code not in (200, 202):
        print(f"  {case.scenario}: council refused {started.status_code} {started.text[:200]}")
        return None, []

    run_id = str(started.json().get("run_id") or "")
    if not run_id:
        return None, []

    # Deliberation is minutes on the GB10, so this waits rather than polls
    # tightly. A run that never finishes is reported rather than hung on.
    #
    # The field is `state`, not `status`. Reading the wrong one meant the loop
    # never saw a finished run and polled a completed deliberation for fifteen
    # minutes before anybody noticed.
    finished = False
    for _ in range(240):
        await asyncio.sleep(5)
        state = await client.get(f"{base}/api/committee/committee/runs/{run_id}", timeout=30.0)
        if state.status_code != 200:
            continue
        if str(state.json().get("state", "")).upper() in ("DONE", "FAILED"):
            finished = True
            break
    if not finished:
        print(f"  {case.scenario}: council {run_id} did not finish; carrying on without it")

    opinions = await client.get(f"{base}/api/committee/committee/runs/{run_id}/opinions", timeout=30.0)
    bodies = (
        [row.get("body") or row for row in (opinions.json().get("opinions") or [])]
        if opinions.status_code == 200
        else []
    )
    print(f"  {case.scenario}: council {run_id} gave {len(bodies)} opinions")
    return run_id, bodies


async def seed_case(
    client: httpx.AsyncClient,
    base: str,
    case: Any,
    corpus: dict[str, list[dict[str, Any]]],
    tampered: set[str],
    *,
    council: bool = True,
) -> str | None:
    chosen = choose_documents(case, corpus, tampered)
    loaded = await load_documents(client, base, case, chosen) if chosen else 0

    inputs = case_inputs(case)
    # The gate engine owns which facts it reads; anything it does not model is
    # dropped rather than sent, so a schema mismatch is a loud 422 here and not
    # a silently ignored field.
    evaluated = await client.post(
        f"{base}/api/policy/policy/evaluate",
        json={
            "product_code": case.snapshot["product_code"],
            "snapshot_id": case.snapshot["snapshot_id"],
            "inputs": inputs,
        },
    )
    if evaluated.status_code != 200:
        print(f"  {case.scenario}: gates refused {evaluated.status_code} {evaluated.text[:300]}")
        return None
    policy_result = evaluated.json()

    tier = case.expected.get("tier", "STANDARD")
    run_id, opinions = await convene(client, base, case, policy_result, tier) if council else (None, [])

    synthesized = await client.post(
        f"{base}/api/policy/policy/synthesize",
        json={
            "product_code": case.snapshot["product_code"],
            "snapshot_id": case.snapshot["snapshot_id"],
            "case_type": "ORIGINATION",
            "tier": tier,
            "requested_amount": str(case.snapshot["amount"]),
            "policy_result": policy_result,
            "factor_scores": factor_scores(case),
            "opinions": opinions,
            "model_versions": case.snapshot.get("model_versions") or {},
            "model_health": "GREEN",
            "active_hardship_arrangement": bool(
                (_tool(case, "hardship.get") or {}).get("active_arrangement")
            ),
        },
    )
    if synthesized.status_code != 200:
        print(f"  {case.scenario}: synthesize refused {synthesized.status_code} {synthesized.text[:300]}")
        return None
    record = synthesized.json()

    # The three narratives, written from the record and from nothing else.
    # Without them the workbench's narrative tab is empty on every case, and a
    # reader has no plain-language account of what the platform decided or why
    # — which is the whole point of having one.
    if council:
        narrated = await client.post(
            f"{base}/api/committee/committee/narrate",
            json={"decision_record": record},
            timeout=240.0,
        )
        if narrated.status_code == 200:
            record["narrative"] = narrated.json()["narrative"]
            status = record["narrative"].get("status", "OK")
            print(f"  {case.scenario}: narrative written ({status})")
        else:
            print(f"  {case.scenario}: narrate refused {narrated.status_code}")

    if run_id:
        # Without this the workbench cannot find the opinions: the record lists
        # their ids and the panel fetches their bodies from the run.
        record["committee_run_id"] = run_id

    appended = await client.post(
        f"{base}/api/decision/recommendations",
        json={
            "decision_record": record,
            "case_id": case.case_id,
            # The real member, not the masked reference. A ledger of five rows
            # all reading «MEMBER_1» is a ledger nobody can follow.
            "member_id": (case.snapshot["member"].get("member_id") or case.snapshot["member"]["member_ref"]),
        },
    )
    if appended.status_code not in (200, 201):
        print(f"  {case.scenario}: ledger refused {appended.status_code} {appended.text[:300]}")
        return None

    print(
        f"  {case.scenario} {case.case_id}: {record['recommendation']} -> {record['route']} "
        f"(score {record['weighted_score']}, gates {len(record['hard_gates'])}, "
        f"documents {loaded})"
    )
    return str(record["decision_record_id"])


async def run(base: str, scenarios: list[str], *, council: bool = True) -> int:
    async with httpx.AsyncClient(timeout=20.0) as anon:
        token = await token_for(anon, "system", base=base)

    corpus = corpus_applications()
    tampered = tampered_applications()
    if not corpus:
        print(f"  no rendered corpus under {CORPUS}; seeding decisions without paper")

    # Only the token is set for every call: httpx picks the content type from
    # the body, and a client-level JSON header would break the file upload.
    headers = {"authorization": f"Bearer {token}"}
    seeded = 0
    async with httpx.AsyncClient(timeout=300.0, headers=headers) as client:
        for case in GOLDEN:
            if scenarios and case.scenario not in scenarios:
                continue
            if await seed_case(client, base, case, corpus, tampered, council=council):
                seeded += 1
    return seeded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://localhost:8000")
    parser.add_argument("--scenario", action="append", default=[], help="seed only these (repeatable)")
    parser.add_argument(
        "--no-council",
        action="store_true",
        help="skip the deliberation. Faster, and the workbench then has no opinions to show.",
    )
    args = parser.parse_args(argv)

    print(f"Seeding golden cases through {args.gateway} ...")
    seeded = asyncio.run(run(args.gateway, args.scenario, council=not args.no_council))
    print(f"\n  {seeded} decision records appended to the ledger")
    return 0 if seeded else 1


if __name__ == "__main__":
    sys.exit(main())

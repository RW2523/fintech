"""Ask the officer copilot twenty-five questions and grade the answers.

    uv run python scripts/copilot_eval.py

Twenty an officer actually asks about a file, and five they must not be
answered (docs/00 T-070). The target is 95% grounded, and the refusals are the
half that matters: a copilot that answers nineteen well and confidently guesses
at the twentieth is worse than one that answers eighteen and says so twice,
because an officer cannot tell which kind of answer they are holding.

Runs against the live model. It is slow and it is the only measurement that
counts: a copilot graded against a fake gateway is a copilot nobody has tested.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai.evals.copilot_questions import GOLDEN, grade  # noqa: E402
from scripts.signin import token_for  # noqa: E402

BASE = "http://localhost:8000"
GROUNDED_TARGET = 0.95


async def pick_case(client: httpx.AsyncClient) -> tuple[str, str | None]:
    """A real decided case, because the copilot reads what is there."""
    queued = await client.get(f"{BASE}/api/decision/queue")
    queued.raise_for_status()
    rows = [row for row in queued.json()["decisions"] if row.get("case_id")]
    if not rows:
        raise SystemExit("  no decided cases; run scripts/seed_demo_case.py first")
    return str(rows[0]["case_id"]), rows[0].get("decision_record_id")


async def main() -> int:
    async with httpx.AsyncClient(timeout=120.0) as anon:
        token = await token_for(anon, "officer", base=BASE)

    async with httpx.AsyncClient(timeout=180.0, headers={"authorization": f"Bearer {token}"}) as client:
        case_id, record_id = await pick_case(client)
        print(f"  asking about {case_id}\n")

        grades = []
        for question in GOLDEN:
            try:
                response = await client.post(
                    f"{BASE}/api/agent_runtime/copilot/ask",
                    json={
                        "case_id": case_id,
                        "question": question.text,
                        "decision_record_id": record_id,
                    },
                )
                answer: dict[str, Any] = response.json()
            except Exception as exc:
                answer = {
                    "grounded": False,
                    "answer": "",
                    "citations": [],
                    "refusal": {"reason": str(exc), "code": "NOT_PERMITTED"},
                }

            result = grade(question, answer)
            grades.append(result)
            mark = "ok  " if result.correct else "MISS"
            shape = "refused" if result.refused else f"{result.citations} cites"
            print(f"  {mark} {question.question_id}  {shape:<10} {question.text[:52]}")
            if result.detail:
                print(f"         {result.detail[:100]}")

    refusals = [g for g in grades if g.question_id.startswith("R")]
    grounded = sum(1 for g in grades if g.grounded)
    correct_refusals = sum(1 for g in refusals if g.correct)

    print(
        f"\n  grounded: {grounded}/{len(grades)} ({grounded / len(grades):.1%}, target {GROUNDED_TARGET:.0%})"
    )
    print(f"  refused what must be refused: {correct_refusals}/{len(refusals)}")

    # Split, because the two numbers mean different things. A case seeded
    # without a committee run carries no opinions, no confidence and no factor
    # scores, so "not recorded" is the correct answer to seven of these and
    # counting it as a miss rewards a model that invents one.
    by_id = {q.question_id: q for q in GOLDEN}
    answerable = [
        g for g in grades if g.question_id.startswith("Q") and not by_id[g.question_id].needs_committee
    ]
    committee = [g for g in grades if g.question_id.startswith("Q") and by_id[g.question_id].needs_committee]
    print(
        f"  answered what this case can answer: "
        f"{sum(1 for g in answerable if not g.refused)}/{len(answerable)}"
    )
    print(
        f"  needed a committee run: "
        f"{sum(1 for g in committee if g.refused)}/{len(committee)} correctly said so"
    )

    ok = grounded / len(grades) >= GROUNDED_TARGET and correct_refusals == len(refusals)
    print("\n  T-070 acceptance:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))

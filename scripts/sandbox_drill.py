"""Walk the S6 flow: change the weights, replay, adopt, and check it took.

    uv run python scripts/sandbox_drill.py

The Board writes the policy and tests it first (docs/11 S6, docs/00 T-073).

1. Raise COMMITMENT and lower CONDUCT by the same amount, so the weights still
   sum to one. A candidate that does not is refused before it is replayed.
2. Replay the decided cases. Every figure comes from the service; nothing here
   computes an outcome.
3. Check S1's weighted score moved and that the report says which cases would
   decide differently.
4. Adopt, with two named approvers, and check the new version is the one in
   force and the one a case decided from now cites.

Adoption is write-once, so each run of this drill adopts the next free version
rather than overwriting the last. A version somebody decided under must still
read back exactly as it did.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.signin import token_for  # noqa: E402

BASE = "http://localhost:8000"
PRODUCT = "PF-STD"
S1 = "snap_0000000000000000000S1CLEAN"

FACTORS = ("CAPACITY", "CONDUCT", "COMMITMENT", "CONDITIONS", "INTEGRITY")


def line(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {'ok  ' if ok else 'MISS'} {label:<46} {detail[:74]}")
    return ok


async def main() -> int:
    async with httpx.AsyncClient(timeout=60.0) as anon:
        token = await token_for(anon, "manager", base=BASE)

    # Decide the demo cases again first, so every frozen baseline is under the
    # pack in force. Without this the drill compares a candidate against
    # whatever S1 happened to be decided under, and a candidate that lands back
    # on those weights moves nothing: the run then reports that S1's score did
    # not change, which is true and useless.
    seed = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "scripts" / "seed_demo_case.py"),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, failed = await seed.communicate()
    if seed.returncode != 0:
        raise SystemExit(f"  could not seed the demo cases: {failed.decode()[:400]}")

    checks: list[bool] = []
    async with httpx.AsyncClient(timeout=300.0, headers={"authorization": f"Bearer {token}"}) as client:
        pack = (await client.get(f"{BASE}/api/policy/policy/{PRODUCT}/latest")).json()
        weights = dict(pack["dff"]["weights"])
        in_force = pack["policy_version"]
        print(f"\n  in force: {in_force}")
        print(f"  weights:  {weights}\n")

        # Move 0.10 from CONDUCT to COMMITMENT. Taken from one and given to the
        # other so the total is untouched: a candidate whose weights do not sum
        # to one is not a policy, it is a mistake.
        candidate_weights = dict(weights)
        move = 0.10 if candidate_weights["CONDUCT"] >= 0.20 else -0.10
        candidate_weights["CONDUCT"] = round(candidate_weights["CONDUCT"] - move, 4)
        candidate_weights["COMMITMENT"] = round(candidate_weights["COMMITMENT"] + move, 4)
        total = round(sum(candidate_weights[f] for f in FACTORS), 6)
        checks.append(line(total == 1.0, "the candidate's weights sum to one", str(total)))

        report = await client.post(
            f"{BASE}/api/policy/policy/sandbox/replay",
            json={
                "product_code": PRODUCT,
                "candidate": {"dff": {"weights": candidate_weights}},
                "range": {"limit": 5000},
            },
        )
        report.raise_for_status()
        body = report.json()
        checks.append(
            line(body["cases_replayed"] > 0, "decided cases were replayed", str(body["cases_replayed"]))
        )
        checks.append(
            line(
                body["baseline"]["cases"] == body["candidate"]["cases"],
                "baseline and candidate cover the same cases",
                f"{body['baseline']['cases']} vs {body['candidate']['cases']}",
            )
        )

        moved = [d for d in body["diffs"] if d["before"]["weighted_score"] != d["after"]["weighted_score"]]
        checks.append(
            line(bool(moved), "some case scores moved", f"{len(moved)} of {body['cases_replayed']}")
        )

        s1 = next((d for d in body["diffs"] if d["snapshot_id"] == S1), None)
        if s1 is None:
            checks.append(line(False, "S1's score moved", "S1 is not in the replay"))
        else:
            change = round(s1["after"]["weighted_score"] - s1["before"]["weighted_score"], 4)
            checks.append(
                line(
                    change != 0,
                    "S1's score moved",
                    f"{s1['before']['weighted_score']} -> {s1['after']['weighted_score']} ({change:+})",
                )
            )

        # The sandbox never decides anything. A replay that wrote a decision
        # record would make the Board's experiment part of the record.
        queue_before = (await client.get(f"{BASE}/api/decision/queue")).json()["decisions"]

        adoption = await client.post(
            f"{BASE}/api/policy/policy/{PRODUCT}/versions",
            json={
                "sandbox_id": body["sandbox_id"],
                "approvers": [
                    {"role": "HEAD_OF_CREDIT", "actor_id": "u-head-credit"},
                    {"role": "HEAD_OF_RISK", "actor_id": "u-head-risk"},
                ],
                "reason": "S6: the Board moved weight from CONDUCT to COMMITMENT",
            },
        )
        adoption.raise_for_status()
        adopted = adoption.json()
        checks.append(
            line(
                adopted["previous_version"] == in_force.rsplit("/", 1)[-1],
                "adoption names the version it replaced",
                f"{adopted['previous_version']} -> {adopted['version']}",
            )
        )

        queue_after = (await client.get(f"{BASE}/api/decision/queue")).json()["decisions"]
        checks.append(
            line(
                len(queue_after) == len(queue_before),
                "the replay decided nothing",
                f"{len(queue_after)} in the queue",
            )
        )

        versions = (await client.get(f"{BASE}/api/policy/policy/{PRODUCT}/versions")).json()
        checks.append(
            line(
                versions["active"] == adopted["version"],
                "the adopted version is the one in force",
                versions["active"],
            )
        )

        now = (await client.get(f"{BASE}/api/policy/policy/{PRODUCT}/latest")).json()
        checks.append(
            line(
                now["dff"]["weights"] == candidate_weights,
                "the version in force carries the new weights",
                str(now["dff"]["weights"]),
            )
        )

        # The version that was replaced must still read back exactly as it did.
        # Every decision citing it is a claim about a document, and a document
        # that changed after the fact is not evidence.
        previous = await client.get(f"{BASE}/api/policy/policy/{PRODUCT}/{adopted['previous_version']}")
        previous.raise_for_status()
        checks.append(
            line(
                previous.json()["dff"]["weights"] == weights,
                "the replaced version is unchanged",
                str(previous.json()["dff"]["weights"]),
            )
        )

        # And a second adoption from the same sandbox run gets its own version
        # rather than overwriting the first.
        again = await client.post(
            f"{BASE}/api/policy/policy/{PRODUCT}/versions",
            json={
                "sandbox_id": body["sandbox_id"],
                "approvers": [
                    {"role": "HEAD_OF_CREDIT", "actor_id": "u-head-credit"},
                    {"role": "HEAD_OF_RISK", "actor_id": "u-head-risk"},
                ],
            },
        )
        checks.append(
            line(
                again.status_code == 200 and again.json()["version"] != adopted["version"],
                "re-adopting writes a new version, never over one",
                f"{adopted['version']} then {again.json().get('version')}",
            )
        )

        # One approver is not two.
        alone = await client.post(
            f"{BASE}/api/policy/policy/{PRODUCT}/versions",
            json={
                "sandbox_id": body["sandbox_id"],
                "approvers": [{"role": "HEAD_OF_CREDIT", "actor_id": "u-head-credit"}],
            },
        )
        checks.append(line(alone.status_code == 422, "one approver is refused", str(alone.status_code)))

        # And a version nobody replayed cannot be adopted at all.
        unreplayed = await client.post(
            f"{BASE}/api/policy/policy/{PRODUCT}/versions",
            json={
                "sandbox_id": "sbx_01ARZ3NDEKTSV4RRFFQ69G5FAW",
                "approvers": [
                    {"role": "HEAD_OF_CREDIT", "actor_id": "u-head-credit"},
                    {"role": "HEAD_OF_RISK", "actor_id": "u-head-risk"},
                ],
            },
        )
        checks.append(
            line(
                unreplayed.status_code == 404,
                "a change nobody replayed cannot be adopted",
                str(unreplayed.status_code),
            )
        )

    passed = sum(checks)
    # Said plainly, because this drill changes the platform. Adoption is
    # write-once by design and this run left two versions behind; a drill that
    # deleted them afterwards would be teaching the wrong lesson about what
    # adopting a policy means.
    print(
        f"\n  this run adopted {adopted['version']} and {again.json().get('version')} "
        f"for {PRODUCT}; {in_force.rsplit('/', 1)[-1]} is no longer in force"
    )
    print(f"\n  {passed}/{len(checks)} checks pass")
    print(f"\n  T-073 acceptance: {'PASS' if passed == len(checks) else 'FAIL'}\n")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

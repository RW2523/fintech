"""The Autonomy Dial as operational state (docs/05 §6, docs/08 §6).

A policy pack is write-once: the rules a decision was made under must still be
readable exactly as they were. How much the platform may act on its own is not
a rule, though. It is a setting an institution turns up and down as it gains or
loses confidence, sometimes several times a week, and it must be changeable
without minting a new pack version and re-approving the credit policy.

So the dial is stored as an amendment to the pack's autonomy document, in
`app_policy.policy_version` with `kind='autonomy'`. That table already exists
for exactly this: a body, who approved it, and when it took effect. The
effective document is the newest active amendment, or the pack's own if there
is none, and either way the DecisionRecord names the version it was decided
under.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "KILL_SWITCH_OWNERS",
    "SETTINGS",
    "TWO_APPROVER_ROLES",
    "AmendmentError",
    "amend",
    "effective_setting",
    "version_for",
]

#: docs/05 §6 — the four positions of the dial, least to most autonomous.
SETTINGS = ("ADVISE", "ASSIST", "ACT_WITH_APPROVAL", "AUTONOMOUS_WITHIN_LIMITS")

#: Who may move the dial. Two of them, and not the same person twice.
TWO_APPROVER_ROLES = frozenset({"HEAD_OF_CREDIT", "HEAD_OF_RISK"})

#: Who may pull the kill switch. One is enough: stopping is not a decision that
#: needs a second opinion, and needing one is how a stop gets delayed.
KILL_SWITCH_OWNERS = frozenset({"HEAD_OF_CREDIT", "HEAD_OF_RISK"})


class AmendmentError(ValueError):
    """The proposed change to the dial is not one that may be made."""


def version_for(product: str, sequence: int) -> str:
    """The version string an amendment carries.

    Distinct from a pack version and obviously so: reading `autonomy@3` in a
    record must not leave anyone wondering which credit policy was in force.
    """
    return f"autonomy/{product}/amendment-{sequence:04d}"


def _approver_roles(approvers: list[dict[str, str]]) -> list[str]:
    return [str(a.get("role", "")).upper() for a in approvers]


def check_approvers(approvers: list[dict[str, str]]) -> None:
    """Two distinct people, both entitled, on a change to how much the
    platform may do without one (docs/08 §6)."""
    if len(approvers) != 2:
        raise AmendmentError(
            f"a dial change needs exactly two approvers, {len(approvers)} given",
        )

    roles = _approver_roles(approvers)
    outside = sorted(set(roles) - TWO_APPROVER_ROLES)
    if outside:
        raise AmendmentError(
            f"{', '.join(outside)} may not approve a dial change; "
            f"expected {' or '.join(sorted(TWO_APPROVER_ROLES))}"
        )

    actors = [str(a.get("actor_id", "")).strip() for a in approvers]
    if any(not actor for actor in actors):
        raise AmendmentError("every approver must be identified")
    if actors[0] == actors[1]:
        # The whole point of two approvers is two people. One person holding
        # both roles approving twice is one person.
        raise AmendmentError(f"{actors[0]} cannot approve their own change twice")


def amend(current: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """The autonomy document as it would be after this change.

    Only the three parts a dial change may touch are taken from the request.
    Everything else, including the action levels and who owns the kill switch,
    comes from the pack: a setting change is not a route into rewriting what
    the platform is allowed to do at all.
    """
    setting = change.get("setting")
    if setting is not None and setting not in SETTINGS:
        raise AmendmentError(f"unknown setting {setting!r}; expected one of {', '.join(SETTINGS)}")

    amended = dict(current)
    if setting is not None:
        amended["setting"] = setting
    if change.get("bands") is not None:
        amended["bands"] = _checked_bands(change["bands"])
    if change.get("conditions") is not None:
        amended["autonomous_conditions"] = {
            **current.get("autonomous_conditions", {}),
            **change["conditions"],
        }
    if change.get("sampling") is not None:
        amended["sampling"] = {**current.get("sampling", {}), **change["sampling"]}
    return amended


def _checked_bands(bands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bands must ascend and the last must be open, as in a pack.

    Checked here as well as in the pack validator because an amendment does not
    go through pack validation, and a descending band list would silently make
    a large case eligible.
    """
    ceilings = [b.get("max_amount") for b in bands]
    if not ceilings:
        raise AmendmentError("a dial change with no bands would make every amount ineligible")
    if ceilings[-1] is not None:
        raise AmendmentError("the last band must be open-ended, or amounts above it have no band")
    if any(c is None for c in ceilings[:-1]):
        raise AmendmentError("only the last band may be open-ended")
    finite = [float(c) for c in ceilings[:-1] if c is not None]
    if finite != sorted(finite):
        raise AmendmentError(f"bands do not ascend: {ceilings}")
    return [dict(b) for b in bands]


def effective_setting(autonomy: dict[str, Any], *, kill_switch: bool) -> str:
    """What the dial reads right now.

    The kill switch does not change the stored setting: it overrides it while
    it is on, so turning it off restores what the institution had chosen
    rather than whatever ADVISE was written over the top of it.
    """
    if kill_switch:
        return str((autonomy.get("kill_switch") or {}).get("effect", {}).get("revert_to", "ADVISE"))
    return str(autonomy.get("setting", "ADVISE"))

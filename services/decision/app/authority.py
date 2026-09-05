"""Authority enforcement at decision time (docs/13 §1).

The UI is never trusted: whatever it showed, decision-service checks the actor's
authority against the record's `required_authority` when the decision arrives.
"""

from __future__ import annotations

from cio_common.auth import ROLES
from cio_common.errors import Forbidden

__all__ = ["AUTHORITY_LADDER", "OVERRIDE_CODES", "check_authority", "outranks"]

#: Ascending. A role may decide a case whose required authority it reaches.
AUTHORITY_LADDER = ("CREDIT_OFFICER", "SENIOR_OFFICER", "CREDIT_COMMITTEE")

#: docs/03 §7. OVR-12 needs a longer explanation.
OVERRIDE_CODES = {f"OVR-{i:02d}" for i in range(1, 13)}
OVR_OTHER_MIN_TEXT = 60
OVR_MIN_TEXT = 20


def _rank(authority_role: str) -> int:
    try:
        return AUTHORITY_LADDER.index(authority_role)
    except ValueError:
        return -1


def outranks(actor_authority: str, required: str) -> bool:
    """True when ``actor_authority`` reaches ``required`` on the ladder."""
    actor_rank, required_rank = _rank(actor_authority), _rank(required)
    if required_rank < 0:
        return False
    return actor_rank >= required_rank


def check_authority(role: str, required: str, final_action: str) -> str:
    """Raise :class:`Forbidden` unless this role may take this action.

    Returns the actor's authority role. Actions that do not commit the
    cooperative to anything (request info, escalate, defer) are open to any
    reviewing role; approve and decline are not.
    """
    authority = ROLES.get(role)
    if authority is None:
        raise Forbidden(f"unknown role {role!r}")

    if role == "compliance" and final_action in ("REQUEST_INFO", "ESCALATE", "DEFER"):
        return authority

    if final_action in ("REQUEST_INFO", "ESCALATE", "DEFER"):
        if _rank(authority) < 0:
            raise Forbidden(f"role {role!r} carries no credit authority", required=required)
        return authority

    if not outranks(authority, required):
        raise Forbidden(
            f"role {role!r} ({authority}) may not {final_action.lower()} a case requiring {required}",
            required=required,
            actor_authority=authority,
        )
    return authority

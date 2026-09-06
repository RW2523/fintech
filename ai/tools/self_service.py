"""Tools the member assistant may use on the member's own record (docs/06 §2.3).

Everything here is deliberately narrow. A member asking about their own money
gets their own money, and nothing in this module can reach anybody else: each
tool declares `member_id`, and the registry pins that argument to the member in
the run scope before the call is made. The assistant never supplies it and
cannot widen it, because the identity comes from the token rather than from the
conversation.

Nothing here returns a score, a recommendation, a factor or a probability. The
member assistant must not state a decision, and the cheapest way to keep that
promise is to give it nothing to state one with.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from ai.tools.client import services
from ai.tools.schemas import ARRAY_OF_OBJECTS, MEMBER_ID, OBJECT, inputs, optional
from cio_tools.registry import tool
from cio_tools.spec import EvidenceSpec, PermittedUse, SideEffect

log = logging.getLogger(__name__)

READ = SideEffect.READ
SERVICING = {PermittedUse.SERVICING}

#: What a member is allowed to be told about one of their accounts. The core
#: record carries more than this, and the rest is none of the assistant's
#: business: arrears strategy, internal status codes and risk grades are for
#: staff, and a member who wants them can ask a person.
ACCOUNT_FIELDS = (
    "account_id",
    "product_code",
    "status",
    "principal",
    "instalment",
    "tenor_months",
    "due_day",
    "opened_at",
)

#: The member's own record in the core. Pointed at `member_id`, which every
#: one of these results carries at the top level.
#:
#: It used to name `account_id`, which is not there: the accounts are nested in
#: a list and the spec could resolve nothing, so no member tool ever produced a
#: citable id. Nothing the assistant said could be cited, and once the output
#: screen stopped treating an empty evidence set as permission to cite
#: anything, every answer became a refusal.
MEMBER_EVIDENCE = EvidenceSpec(
    type="CORE_FIELD",
    source_system="core_stub",
    source_record_path="member_id",
    locator_from={"field_path": "member_id"},
)


def _account(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in ACCOUNT_FIELDS if field in row}


@tool(
    "get_my_balance",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=OBJECT,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="core_stub",
    evidence=MEMBER_EVIDENCE,
)
async def get_my_balance(member_id: str) -> dict[str, Any]:
    """What this member has saved, and where each financing has got to.

    Both, because "what is my balance" means the financing to somebody paying
    one off and the savings to somebody who is not, and an assistant that
    guesses which will be wrong for half the membership.

    No outstanding figure is computed. The core record holds the original
    principal, the instalment and the schedule; it does not hold a settlement
    balance, and a number this tool derived would be one the cooperative could
    not stand behind if the member wrote it down. Instalments paid out of
    instalments due is what the record actually supports, so that is what is
    returned.
    """
    accounts = await services().get("core_stub", f"/core/members/{member_id}/accounts")
    savings = await services().get("core_stub", f"/core/members/{member_id}/savings")
    latest = max(savings or [], key=lambda point: str(point.get("as_of") or ""), default=None)

    rows: list[dict[str, Any]] = []
    for row in accounts or []:
        account = _account(row)
        account_id = row.get("account_id")
        if account_id:
            account |= await _instalments_paid(str(account_id))
        rows.append(account)

    return {
        "member_id": member_id,
        "accounts": rows,
        "savings_balance": (latest or {}).get("balance"),
        "savings_as_of": (latest or {}).get("as_of"),
        # Said in words because a gap in a tool result is an invitation. Asked
        # for a balance with only a principal and an instalment on hand, the
        # model computed a settlement figure and the screen caught it; asked
        # the same question with this sentence present, it has something true
        # to repeat instead.
        "note": (
            "The amount still to pay is not held on this record. Give the savings "
            "balance and the instalments paid, and say a colleague can confirm a "
            "settlement figure."
        ),
    }


async def _instalments_paid(account_id: str) -> dict[str, Any]:
    """How far through the schedule this account is.

    A payment is matched to the instalment it settled, so a reversed one does
    not count: a member whose direct debit bounced has not paid that month, and
    telling them they have is how somebody misses an arrears letter.
    """
    schedule = await services().get("core_stub", f"/core/accounts/{account_id}/schedule")
    payments = await services().get("core_stub", f"/core/accounts/{account_id}/payments")
    settled = {str(payment.get("schedule_id")) for payment in payments or [] if not payment.get("reversed")}
    total = len(schedule or [])
    paid = sum(1 for row in schedule or [] if str(row.get("schedule_id")) in settled)
    return {"instalments_total": total, "instalments_paid": paid}


@tool(
    "get_my_next_payment",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID, as_of=optional({"type": "string", "format": "date"})),
    output_schema=OBJECT,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="core_stub",
    evidence=MEMBER_EVIDENCE,
)
async def get_my_next_payment(member_id: str, as_of: str | None = None) -> dict[str, Any]:
    """The next instalment falling due, and anything already past due.

    Past due is returned alongside rather than instead. A member who is behind
    and asks about their next payment is asking the wrong question, and an
    answer that gives only the next date lets them keep asking it.
    """
    today = as_of or date.today().isoformat()
    accounts = await services().get("core_stub", f"/core/members/{member_id}/accounts")
    upcoming: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    for row in accounts or []:
        account_id = row.get("account_id")
        if not account_id:
            continue
        schedule = await services().get("core_stub", f"/core/accounts/{account_id}/schedule")
        payments = await services().get("core_stub", f"/core/accounts/{account_id}/payments")
        settled = {
            str(payment.get("schedule_id")) for payment in payments or [] if not payment.get("reversed")
        }
        for instalment in schedule or []:
            if str(instalment.get("schedule_id")) in settled:
                continue
            due = str(instalment.get("due_date") or "")
            entry = {
                "account_id": account_id,
                "due_date": due,
                "amount_due": instalment.get("amount_due"),
            }
            (upcoming if due >= today else overdue).append(entry)
    upcoming.sort(key=lambda entry: str(entry["due_date"]))
    overdue.sort(key=lambda entry: str(entry["due_date"]))
    note = "The next instalment above is the only date on file. Do not derive any other."
    if not upcoming:
        # A null next payment was read as a blank to fill: the assistant
        # answered with a date built from each account's `due_day`, which is
        # the day of the month a facility falls due and not a scheduled
        # instalment. It told a member a payment date that no row supports.
        note = (
            "Every instalment on the schedule has been paid and no further "
            "instalment is on file. There is no next payment date to give."
        )
    return {
        "member_id": member_id,
        "as_of": today,
        "next_payment": upcoming[0] if upcoming else None,
        "overdue": overdue,
        "overdue_count": len(overdue),
        "note": note,
    }


@tool(
    "get_my_application",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=OBJECT,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="application",
    evidence=MEMBER_EVIDENCE,
)
async def get_my_application(member_id: str) -> dict[str, Any]:
    """Where this member's application has got to, in the words a member uses.

    Deliberately not the case's internal state. `OFFICER_REVIEW` is a routing
    decision and telling a member their application is under officer review
    invites them to ask what the officer thinks, which nobody here may answer.
    """
    entries = [_application(row) for row in await _applications_for(member_id)]
    return {"member_id": member_id, "applications": entries, "count": len(entries)}


async def _applications_for(member_id: str) -> list[dict[str, Any]]:
    """Everything this member has applied for, from both places it can live.

    The application service owns what was submitted through this platform. The
    incumbent core owns what was submitted before it, which for the generated
    population is all six hundred of them: a member assistant that reads only
    the first cannot see the application the member actually made, and after a
    reset it found nothing at all.
    """
    found: list[dict[str, Any]] = []
    try:
        body = await services().get("application", "/applications/by-member", member_id=member_id)
        rows = body.get("applications") if isinstance(body, dict) else body
        found.extend(rows or [])
    except Exception as exc:
        # One source being down must not hide the other, and it must not be
        # silent either: an assistant that says "you have no application"
        # because a service was unreachable has told a member something false.
        log.warning("the application service could not be read for %s: %s", member_id, exc)

    if not found:
        core = await services().get("core_stub", f"/core/members/{member_id}/applications")
        found.extend(core or [])
    return found


def _application(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "application_id": row.get("application_id"),
        "product": row.get("product_code"),
        "amount": row.get("amount"),
        "submitted_at": row.get("submitted_at") or row.get("created_at"),
        "stage": _member_stage(row),
    }


#: Internal case states, said the way a member would say them. Anything not
#: listed is reported as "being looked at", which is true of every state this
#: mapping does not know and is never a statement about the outcome.
MEMBER_STAGE = {
    "DRAFT": "not sent to us yet",
    "OPEN": "being looked at",
    "SUBMITTED": "received, waiting to be looked at",
    "IN_ASSESSMENT": "being looked at",
    "AWAITING_DOCUMENTS": "waiting for documents from you",
    "DECIDED": "decided — we will write to you",
    "CLOSED": "closed",
    "WITHDRAWN": "withdrawn",
}


def _member_stage(row: dict[str, Any]) -> str:
    """The case state, or the application's own status when there is no case.

    Falls through to "being looked at" for anything unrecognised, which is true
    of every state this mapping does not know and is never a statement about
    the outcome.
    """
    state = str(row.get("state") or row.get("status") or "").upper()
    return MEMBER_STAGE.get(state, "being looked at")


@tool(
    "get_missing_documents",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=OBJECT,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="application",
    evidence=MEMBER_EVIDENCE,
)
async def get_missing_documents(member_id: str) -> dict[str, Any]:
    """What the member still has to send.

    Missing means required by the product's published policy pack and not on
    the case. Deriving it from the pack rather than from a list somebody
    maintains means the answer cannot drift from the rule it comes from: if the
    pack stops requiring employment confirmation, this stops asking for it the
    same day.
    """
    outstanding: list[dict[str, Any]] = []
    for row in await _applications_for(member_id):
        case_id = row.get("case_id")
        product = row.get("product_code")
        if not case_id or not product:
            continue
        pack = await services().get("policy", f"/policy/{product}/latest")
        # The pack comes back wrapped: policy, dff and autonomy are three
        # documents and only the first one lists what a member must send.
        policy = (pack or {}).get("policy") or {}
        required = (policy.get("documents") or {}).get("required") or []
        held = await services().get("document", f"/cases/{case_id}/documents")
        documents = held.get("documents") if isinstance(held, dict) else held
        present = {str(doc.get("type") or "").upper() for doc in documents or [] if isinstance(doc, dict)}
        outstanding.extend(
            {
                "case_id": case_id,
                "document_type": wanted,
                "why": DOCUMENT_REASON.get(wanted, "our lending policy asks for it on this product"),
            }
            for wanted in required
            if str(wanted).upper() not in present
        )
    return {"member_id": member_id, "missing": outstanding, "count": len(outstanding)}


#: Why each document is wanted, in a sentence a member can act on. "We need a
#: payslip" is an instruction; "we need a payslip because the income on your
#: form has not been confirmed" is something somebody can actually do.
DOCUMENT_REASON = {
    "IDENTITY": "we have to confirm who you are before we can lend",
    "PAYSLIP_LATEST_3": "to confirm the income you put on your form",
    "EMPLOYMENT_CONFIRMATION": "to confirm you are still with the employer you named",
    "BANK_STATEMENT_3M": "to see your regular commitments",
    "PROVIDENT_FUND_STATEMENT": "to confirm the savings you told us about",
}


@tool(
    "product_info.get",
    version="1.0",
    input_schema=inputs(product=optional({"type": "string", "maxLength": 64})),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=SERVICING,
    side_effects=READ,
    backing_service="policy",
)
async def product_info_get(product: str | None = None) -> list[dict[str, Any]]:
    """What the cooperative offers and on what published terms.

    Terms only. The eligibility rules a pack carries are policy, and a member
    reading them back as "so I qualify" is the failure this whole agent exists
    to avoid, so nothing here says who is eligible.
    """
    listed = await services().get("policy", "/policy/products")
    codes = listed.get("products") if isinstance(listed, dict) else listed
    wanted = [product] if product else list(codes or [])
    rows: list[dict[str, Any]] = []
    for code in wanted:
        pack = await services().get("policy", f"/policy/{code}/latest")
        if not isinstance(pack, dict):
            continue
        policy = pack.get("policy") or {}
        terms = policy.get("product_terms") or {}
        rows.append(
            {
                "product": policy.get("product", code),
                "name": policy.get("name"),
                "currency": policy.get("currency"),
                "min_amount": terms.get("min_amount"),
                "max_amount": terms.get("max_amount"),
                "min_tenor_months": terms.get("min_tenor"),
                "max_tenor_months": terms.get("max_tenor"),
                "profit_rate": terms.get("profit_rate"),
                "purposes_allowed": terms.get("purposes_allowed"),
            }
        )
    return rows


@tool(
    "request_callback",
    version="1.0",
    input_schema=inputs(
        member_id=MEMBER_ID,
        reason={"type": "string", "minLength": 3, "maxLength": 400},
        urgency=optional({"type": "string", "enum": ["ROUTINE", "PRIORITY"]}),
        # Set by the hardship classifier, not by the model. The classifier runs
        # before the model is called and short-circuits when it fires, so the
        # assistant only ever reaches this tool on an ordinary "can I talk to
        # somebody", where the signal is ROUTINE and stays that way.
        signal=optional(
            {
                "type": "string",
                "enum": ["ROUTINE", "HARDSHIP", "COMPLAINT", "BEREAVEMENT", "VULNERABILITY"],
            }
        ),
        # What the member actually wrote. Somebody reading a queue of rows all
        # saying "difficulty paying" cannot tell a missed instalment from an
        # eviction notice, and the paraphrase is what loses the difference.
        said=optional({"type": "string", "maxLength": 4000}),
    ),
    output_schema=OBJECT,
    purpose_tags=SERVICING,
    side_effects=SideEffect.WRITE_PROPOSAL,
    backing_service="notification",
)
async def request_callback(
    member_id: str,
    reason: str,
    urgency: str | None = None,
    signal: str | None = None,
    said: str | None = None,
) -> dict[str, Any]:
    """Put this member in front of a person.

    A proposal, not a call. It creates the task and says so; a human picks it
    up. The assistant is never the last thing between a member in difficulty
    and somebody who can actually help them.
    """
    return await services().post(
        "notification",
        "/handoffs",
        {
            "member_id": member_id,
            "reason": reason,
            "urgency": urgency or "ROUTINE",
            "signal": signal or "ROUTINE",
            "said": said,
        },
    )

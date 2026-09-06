"""Generating the policy corpus agents retrieve from (docs/10 §10, docs/06 §7).

The corpus is prose: what an officer would read if they opened the credit
policy. It is generated from the policy packs rather than written beside them,
because the whole value of retrieval here is that the clause an agent cites is
the clause the engine actually ran. A corpus maintained by hand drifts from the
rules within a release, and then a citation is worse than none: it looks like
provenance and is not.

Clause ids are the rule ids. `ELG-02` in the policy pack is `ELG-02` in the
document, so a citation can be checked against what was evaluated.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = ["CORPUS_ROOT", "PACK_ROOT", "Document", "build_corpus"]

ROOT = Path(__file__).resolve().parents[1]
PACK_ROOT = ROOT / "policy_packs"
CORPUS_ROOT = ROOT / "synthetic" / "policy_corpus"

#: Section heading per rule family, and the sentence that introduces it.
SECTIONS: dict[str, tuple[str, str]] = {
    "eligibility": (
        "Eligibility",
        "Whether the cooperative may lend to this member at all. "
        "A failure here is not a judgement about the member's "
        "creditworthiness; it means the product is not available "
        "to them on these terms.",
    ),
    "documents": (
        "Documents",
        "What the file must contain before a decision can be made. "
        "A missing or unreadable document is a request for "
        "information, never an accusation.",
    ),
    "shariah": (
        "Shariah compliance",
        "Conditions the Shariah board sets for this product. A breach "
        "stops the case; it is not traded off against a strong score.",
    ),
    "affordability": (
        "Affordability",
        "Whether the member can carry the repayment. Measured "
        "from verified income and existing commitments, and "
        "stressed to see what happens if either moves.",
    ),
    "exposure": (
        "Exposure",
        "How much the cooperative may have outstanding to one member or one employer at a time.",
    ),
    "routing": (
        "Routing",
        "Where a case goes once it has been assessed. Routing is a "
        "decision about who decides, not about the outcome.",
    ),
}

#: The rule outcome, in words an officer would use.
OUTCOMES = {
    "INELIGIBLE": "the application cannot proceed on these terms",
    "BLOCK_NORMAL_PATH": "the case leaves the normal path and goes to a person",
    "REFER": "the case is referred for review",
    "CONDITION": "the approval carries a condition",
}


@dataclass(frozen=True, slots=True)
class Document:
    """One corpus file."""

    path: Path
    product: str
    version: str
    clauses: tuple[str, ...]


def _front_matter(**fields: Any) -> str:
    return "---\n" + yaml.safe_dump(fields, sort_keys=False).strip() + "\n---\n"


def _reason_wording() -> dict[str, str]:
    """The officer-facing sentence for each approved reason code.

    Included in every clause so the document says in words what the expression
    says in symbols. Without it the searchable nouns live only inside
    identifiers like `member.tenure_months`, and a question about "minimum
    membership tenure" matches nothing.
    """
    path = ROOT / "contracts" / "reason_codes.yaml"
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text())
    return {
        code: str(body.get("officer") or "")
        for group in loaded["groups"].values()
        for code, body in group["codes"].items()
    }


def _clause(rule: dict[str, Any], family: str) -> str:
    """One rule, as a clause a person can read and an agent can cite.

    Rules come in two shapes. A gate has a `rule` that must hold; a routing
    rule has a `when` that sends the case somewhere. Both are clauses, and
    both are cited the same way, so the difference is in the wording rather
    than in the structure.
    """
    reads = ", ".join(f"`{r}`" for r in rule.get("reads") or [])
    heading = SECTIONS.get(family, (family.capitalize(), ""))[0]
    wording = _reason_wording().get(str(rule.get("reason_code", rule["id"])), "")
    body = [f"### {rule['id']}", ""]
    if wording:
        body += [f"*{heading}: {wording.lower()}.*", ""]

    if "when" in rule:
        body += [
            f"**Condition.** This clause applies when `{str(rule['when']).strip()}`.",
            "",
            f"**Effect.** The case is routed to `{rule.get('route', 'OFFICER_REVIEW')}`.",
        ]
        if "on_fail" in rule:
            body += [
                "",
                f"**Additionally.** "
                f"{OUTCOMES.get(str(rule['on_fail']), 'the case is referred').capitalize()}.",
            ]
    else:
        outcome = OUTCOMES.get(str(rule.get("on_fail", "")), "the case is referred for review")
        body += [
            f"**Condition.** The case satisfies this clause when `{str(rule['rule']).strip()}`.",
            "",
            f"**If it does not.** {outcome.capitalize()}, under reason code "
            f"`{rule.get('reason_code', rule['id'])}`.",
        ]
    if reads:
        body += [
            "",
            f"**Inputs.** The clause is evaluated from {reads}. "
            "Every one of them carries evidence back to the record it "
            "came from.",
        ]
    if note := rule.get("note"):
        body += ["", f"**Note.** {note}"]
    return "\n".join(body)


def _terms_table(terms: dict[str, Any], currency: str) -> str:
    money = {"min_amount", "max_amount"}
    rows = ["| Term | Value |", "|---|---|"]
    for key, value in terms.items():
        if isinstance(value, list):
            shown = ", ".join(str(v) for v in value)
        elif key in money:
            shown = f"{value:,} {currency}"
        elif key == "profit_rate":
            shown = f"{float(value):.3%}"
        else:
            shown = str(value)
        rows.append(f"| {key.replace('_', ' ')} | {shown} |")
    return "\n".join(rows)


def _product_document(pack: dict[str, Any]) -> tuple[str, list[str]]:
    product = pack["product"]
    parts = [
        _front_matter(
            product=product,
            version=pack["version"],
            effective_from=str(pack.get("effective_from", "")),
            doc=f"product_{product}",
        ),
        f"# {pack['name']} ({product})",
        "",
        f"This sheet describes {pack['name']} as it stands in version "
        f"{pack['version']}, effective {pack.get('effective_from', 'on issue')}. "
        "Every clause id below is the id the decision engine evaluates, so a "
        "decision that cites a clause can be checked against the rule that ran.",
        "",
        "## Terms",
        "",
        _terms_table(pack.get("product_terms") or {}, pack.get("currency", "LCU")),
    ]
    clauses: list[str] = []
    for family, (heading, intro) in SECTIONS.items():
        section = pack.get(family) or {}
        rules = section.get("rules") if isinstance(section, dict) else None
        if not rules:
            continue
        parts += ["", f"## {heading}", "", intro, ""]
        for rule in rules:
            parts.append(_clause(rule, family))
            parts.append("")
            clauses.append(str(rule["id"]))
    return "\n".join(parts).rstrip() + "\n", clauses


def _credit_policy(packs: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The policy every product inherits, assembled from what they share."""
    reference = packs[0]
    parts = [
        _front_matter(
            product="ALL",
            version=reference["version"],
            effective_from=str(reference.get("effective_from", "")),
            doc="credit_policy",
        ),
        "# Credit policy",
        "",
        "The rules below apply to every financing product the cooperative "
        "offers. A product sheet may add to them; none may relax them.",
        "",
        "## How a case is decided",
        "",
        "A case passes through the same sequence every time, and the order is "
        "not a preference. Hard gates are checked first, because a case that "
        "cannot proceed should not be scored. Evidence validity is checked "
        "next, because a score built on a document nobody could read is not a "
        "score. Only then are the Decision Factors weighted, and only then is "
        "the case routed.",
        "",
        "A model never decides. It produces a number, that number is an input "
        "to a factor, and the factors are combined by a formula that is written "
        "down and versioned. Where a decision was close, the record says which "
        "factor decided it and what would have changed the outcome.",
        "",
    ]
    clauses: list[str] = []
    for family, (heading, intro) in SECTIONS.items():
        rules: dict[str, dict[str, Any]] = {}
        for pack in packs:
            section = pack.get(family) or {}
            for rule in (section.get("rules") if isinstance(section, dict) else []) or []:
                rules.setdefault(str(rule["id"]), rule)
        if not rules:
            continue
        parts += ["", f"## {heading}", "", intro, ""]
        for rule_id in sorted(rules):
            parts.append(_clause(rules[rule_id], family))
            parts.append("")
            clauses.append(rule_id)
    return "\n".join(parts).rstrip() + "\n", clauses


def _authority_matrix(pack: dict[str, Any]) -> tuple[str, list[str]]:
    """Who may approve what, as clauses rather than a bare table.

    The bands in the pack are amount ceilings by role. Each becomes its own
    clause so an agent can cite the authority it relied on, the same way it
    cites an eligibility rule.
    """
    authority = pack.get("authority") or {}
    currency = pack.get("currency", "LCU")
    parts = [
        _front_matter(
            product="ALL",
            version=pack["version"],
            effective_from=str(pack.get("effective_from", "")),
            doc="authority_matrix",
        ),
        "# Authority matrix",
        "",
        "Who may approve what. Authority is about the size of the commitment "
        "and the risk it carries, not about how confident anyone feels. A "
        "decision above a role's ceiling is not made and then reviewed; it is "
        "made by whoever holds the authority.",
        "",
    ]
    clauses: list[str] = []

    bands = [b for b in (authority.get("bands") or []) if isinstance(b, dict)]
    if bands:
        parts += ["## Approval ceilings", "", "| Role | Approves up to |", "|---|---|"]
        for band in bands:
            ceiling = band.get("max_amount")
            shown = "no ceiling" if ceiling in (None, "") else f"{ceiling:,} {currency}"
            parts.append(f"| {band.get('role', '')} | {shown} |")
        parts.append("")

        for index, band in enumerate(bands, start=1):
            clause_id = f"AUT-{index:02d}"
            clauses.append(clause_id)
            ceiling = band.get("max_amount")
            if ceiling in (None, ""):
                effect = "approves any amount, and is the authority for every case above the other ceilings"
                caveat = ""
            else:
                effect = f"approves up to {ceiling:,} {currency}"
                caveat = (
                    " A case above that passes to the next authority "
                    "rather than being split or approved in parts."
                )
            parts += [
                f"### {clause_id}",
                "",
                f"**{band.get('role', 'Role')}.** This role {effect}." + caveat,
                "",
            ]

    if approver := authority.get("exception_approver"):
        clauses.append("AUT-EX")
        parts += [
            "### AUT-EX",
            "",
            f"**Policy exceptions.** An exception to any clause in this "
            f"policy is approved by {approver} and by nobody below that "
            "role. The exception is recorded against the case with the "
            "clause it departs from and the reason, so a later reader "
            "can see both what the policy said and why it was set "
            "aside.",
            "",
        ]
    return "\n".join(parts).rstrip() + "\n", clauses


PROSE: dict[str, tuple[str, str]] = {
    "collections_procedure": (
        "Collections procedure",
        """
        Collections begins the day a payment is missed, not the day an account
        is written off. The first contact is a reminder, not a demand: most
        missed payments are administrative, and treating them as delinquency
        costs the relationship more than it recovers.

        ### COL-01 First contact

        A reminder goes out on the first missed cycle, through the member's
        preferred channel. It states the amount, the cycle it belongs to and
        how to pay. It does not threaten, and it does not mention consequences
        that have not been decided.

        ### COL-02 Understanding before action

        Before any escalation, the officer establishes why the payment was
        missed. A member whose employer's deduction file was late is not in
        difficulty, and the platform will already have flagged the outage.

        ### COL-03 Arrangement

        Where a member cannot pay in full, an arrangement is offered before any
        formal step. An arrangement is a promise the cooperative intends to
        keep as much as the member does: its terms are recorded, and the
        account is not escalated while it is being met.

        ### COL-04 Escalation

        Escalation follows only where contact has failed or an arrangement has
        broken without explanation. It is a decision by a person, recorded with
        the reason, and it is reversible.

        ### COL-05 Guarantors

        A guarantor is approached only after the member has been given a
        genuine opportunity to resolve the arrears. Approaching a guarantor
        first damages two relationships to save time on one.
        """,
    ),
    "hardship_policy": (
        "Hardship policy",
        """
        Hardship is a change in a member's circumstances that makes the agreed
        repayment unaffordable through no fault of their own. The cooperative's
        position is that a member in hardship is still a member.

        ### HRD-01 What counts

        Loss of employment, reduction in hours, illness, bereavement, or a
        household emergency. The list is not exhaustive, and an officer may
        recognise hardship that is not on it.

        ### HRD-02 Evidence

        Enough to establish that the change happened, and no more. The
        cooperative does not require medical detail, and it does not require a
        member to prove hardship they have already described consistently.

        ### HRD-03 What may be offered

        A payment holiday, a reduced instalment for a fixed period, a tenor
        extension, or a combination. The offer is chosen for the member's
        circumstances rather than for the cooperative's book.

        ### HRD-04 What is not affected

        A hardship arrangement is not an arrears event, and it does not by
        itself lower a member's conduct score. A member who asks for help early
        has behaved well, and the record should say so.

        ### HRD-05 Review

        Every hardship arrangement is reviewed before it ends, with the member,
        and the review is a conversation rather than a reassessment.
        """,
    ),
}


def _prose_document(name: str, title: str, body: str, version: str) -> tuple[str, list[str]]:
    text = textwrap.dedent(body).strip()
    clauses = [line.split()[1] for line in text.splitlines() if line.startswith("### ")]
    content = _front_matter(product="ALL", version=version, doc=name) + f"# {title}\n\n" + text + "\n"
    return content, clauses


def build_corpus(out: Path = CORPUS_ROOT, *, packs: Path = PACK_ROOT) -> list[Document]:
    """Write the corpus. Returns what was written, for the indexer and tests."""
    out.mkdir(parents=True, exist_ok=True)
    loaded: list[dict[str, Any]] = []
    for path in sorted(packs.glob("*/*/policy.yaml")):
        loaded.append(yaml.safe_load(path.read_text()))
    if not loaded:
        raise FileNotFoundError(f"no policy packs under {packs}")

    written: list[Document] = []

    for pack in loaded:
        content, clauses = _product_document(pack)
        # One file per version, not per product. Every version was writing to
        # the same name, so the corpus described whichever pack sorted last and
        # `policy.lookup` would answer a question about the version in force
        # with clauses from a version nobody decided under. The index carries
        # the version per chunk and callers can filter on it, so the versions
        # are kept side by side rather than collapsed.
        path = out / f"product_{pack['product']}_{pack['version']}.md"
        path.write_text(content)
        written.append(
            Document(path=path, product=pack["product"], version=pack["version"], clauses=tuple(clauses))
        )

    # The cross-product documents describe policy as it stands, so they are
    # written from the newest version of each product rather than from every
    # one: a credit policy that appeared four times, once per adopted version,
    # would be four near-identical answers to the same question.
    newest: dict[str, dict[str, Any]] = {}
    for pack in loaded:
        current = newest.get(pack["product"])
        if current is None or str(pack["version"]) >= str(current["version"]):
            newest[pack["product"]] = pack
    loaded = [newest[product] for product in sorted(newest)]

    content, clauses = _credit_policy(loaded)
    path = out / "credit_policy.md"
    path.write_text(content)
    written.append(Document(path=path, product="ALL", version=loaded[0]["version"], clauses=tuple(clauses)))

    content, clauses = _authority_matrix(loaded[0])
    path = out / "authority_matrix.md"
    path.write_text(content)
    written.append(Document(path=path, product="ALL", version=loaded[0]["version"], clauses=tuple(clauses)))

    for name, (title, body) in PROSE.items():
        content, clauses = _prose_document(name, title, body, loaded[0]["version"])
        path = out / f"{name}.md"
        path.write_text(content)
        written.append(
            Document(path=path, product="ALL", version=loaded[0]["version"], clauses=tuple(clauses))
        )
    return written

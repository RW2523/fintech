"""Message templates, in the member's language (docs/07 §5).

Templates rather than generated text, and the reason is not cost. A message to
a member about money they owe is a thing the cooperative said, and it has to be
the same thing every time, reviewable before it is ever sent, and in a language
somebody signed off. A model writing each one produces prose nobody approved.

Variables are substituted, never interpolated from a model's output: every
value in a message comes from the case file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["CADENCE_TEMPLATES", "TEMPLATES", "Template", "missing_variables", "render"]


@dataclass(frozen=True, slots=True)
class Template:
    """One message, in one language."""

    template_id: str
    language: str
    subject: str
    body: str
    #: What the body needs. Checked before sending, because a message that
    #: reaches a member with "your instalment of {amount}" in it is worse than
    #: one that was never sent.
    variables: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "language": self.language,
            "subject": self.subject,
            "body": self.body,
            "variables": list(self.variables),
        }


def _template(template_id: str, language: str, subject: str, body: str) -> Template:
    return Template(
        template_id=template_id,
        language=language,
        subject=subject,
        body=body,
        variables=tuple(sorted(set(re.findall(r"\{(\w+)\}", subject + body)))),
    )


#: docs/07 §5 — the reminder cadence, one template per offset, plus the two
#: that are not reminders at all.
#:
#: The tone is deliberate. A reminder before the due date is a courtesy and
#: reads like one; the day-after message states a fact and asks a question. A
#: cooperative's members own it, and a demand letter from an organisation
#: somebody part-owns is a category error.
_ENGLISH = [
    _template(
        "REM_14",
        "en",
        "Your instalment is due on {due_date}",
        "Hello {member_name},\n\n"
        "Your instalment of {amount} for account {account_ref} is due on {due_date}.\n"
        "Nothing is needed from you if your salary deduction is in place.\n\n"
        "{cooperative_name}",
    ),
    _template(
        "REM_7",
        "en",
        "A week until your instalment on {due_date}",
        "Hello {member_name},\n\n"
        "Your instalment of {amount} for account {account_ref} is due in a week, "
        "on {due_date}.\n\n"
        "{cooperative_name}",
    ),
    _template(
        "REM_3_PRIORITY",
        "en",
        "Your instalment is due on {due_date}",
        "Hello {member_name},\n\n"
        "Your instalment of {amount} for account {account_ref} is due on {due_date}.\n"
        "If paying on time will be difficult this month, please tell us before the date "
        "rather than after. There is usually something we can arrange, and it is easier "
        "beforehand.\n\n"
        "{cooperative_name}",
    ),
    _template(
        "DUE",
        "en",
        "Your instalment is due today",
        "Hello {member_name},\n\n"
        "Your instalment of {amount} for account {account_ref} is due today.\n\n"
        "{cooperative_name}",
    ),
    _template(
        "OVERDUE_1",
        "en",
        "We have not received your instalment",
        "Hello {member_name},\n\n"
        "We have not received your instalment of {amount} for account {account_ref}, "
        "which was due on {due_date}.\n"
        "If it is on its way, thank you and please ignore this. If something has changed, "
        "please tell us: we would rather arrange something than let it drift.\n\n"
        "{cooperative_name}",
    ),
    _template(
        "DEDUCTION_MISSED_MEMBER",
        "en",
        "Your salary deduction did not reach us this month",
        "Hello {member_name},\n\n"
        "Your salary deduction for account {account_ref} did not reach us this month. "
        "This is usually the employer's payroll rather than anything you have done, and "
        "we are asking them.\n"
        "You do not need to do anything yet. We will write again once we know more.\n\n"
        "{cooperative_name}",
    ),
    _template(
        "OFFICER_OUTREACH",
        "en",
        "A note from {cooperative_name}",
        "Hello {member_name},\n\n{message}\n\n{officer_name}\n{cooperative_name}",
    ),
]

#: A second language, to prove the field is real rather than decorative. The
#: member's language is stored on their record and the platform uses it; a
#: message in a language somebody does not read is a message nobody sent.
_OTHER = [
    _template(
        "REM_14",
        "lang_b",
        "[lang_b] Your instalment is due on {due_date}",
        "[lang_b] Hello {member_name}. Your instalment of {amount} for account "
        "{account_ref} is due on {due_date}.\n\n{cooperative_name}",
    ),
    _template(
        "OVERDUE_1",
        "lang_b",
        "[lang_b] We have not received your instalment",
        "[lang_b] Hello {member_name}. We have not received your instalment of {amount} "
        "for account {account_ref}, due on {due_date}. Please tell us if something has "
        "changed.\n\n{cooperative_name}",
    ),
]

TEMPLATES: dict[tuple[str, str], Template] = {
    (template.template_id, template.language): template for template in (*_ENGLISH, *_OTHER)
}

#: docs/07 §5 — which template goes with which offset from the due date.
CADENCE_TEMPLATES: dict[int, str] = {
    -14: "REM_14",
    -7: "REM_7",
    -3: "REM_3_PRIORITY",
    0: "DUE",
    1: "OVERDUE_1",
}

DEFAULT_LANGUAGE = "en"


def template_for(template_id: str, language: str) -> Template:
    """The template in the member's language, or English with that recorded.

    Falling back rather than failing: a member whose language has no
    translation still needs the reminder. What must not happen silently is the
    fallback itself, so the caller records which language was actually used.
    """
    found = TEMPLATES.get((template_id, language))
    if found is not None:
        return found
    fallback = TEMPLATES.get((template_id, DEFAULT_LANGUAGE))
    if fallback is None:
        raise KeyError(f"no template {template_id!r} in any language")
    return fallback


def missing_variables(template: Template, variables: dict[str, Any]) -> list[str]:
    return [name for name in template.variables if name not in variables]


def render(template: Template, variables: dict[str, Any]) -> tuple[str, str]:
    """Subject and body, with every variable substituted.

    Refuses rather than rendering a gap. A message reaching a member with
    "your instalment of {amount}" in it is worse than one that was never sent:
    the first is the cooperative looking broken to somebody it is asking for
    money.
    """
    absent = missing_variables(template, variables)
    if absent:
        raise ValueError(f"{template.template_id} needs {', '.join(absent)}")

    safe = {name: str(variables[name]) for name in template.variables}
    return template.subject.format(**safe), template.body.format(**safe)

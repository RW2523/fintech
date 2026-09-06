"""Masking personal data before it reaches a model (docs/06 §6, docs/13 §2).

A model does not need to know who someone is to reason about their case. So
identifying values are replaced with stable placeholders before the request
leaves the platform and put back in the answer, and the model is asked to
reason about «NAME_1» rather than about a person.

Two mechanisms, because one is not enough. Structured identifiers have shapes
and are found by pattern. Names and addresses do not, so the caller passes the
values it already knows from the case record; the gateway will not guess at a
name, because guessing wrong either leaks a name or corrupts the prompt.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["KINDS", "MaskMap", "mask_text", "unmask_text"]

#: The placeholder shape. Guillemets are used because a model rarely produces
#: them by accident, so an unmasked answer is easy to tell from a masked one.
#: noqa reason: this is a placeholder format, not a credential.
_PLACEHOLDER = "«{kind}_{index}»"
_PLACEHOLDER_PATTERN = re.compile(r"«([A-Z_]+)_(\d+)»")

#: Structured identifiers, found by shape. Ordered: the first pattern that
#: matches a span wins, so the more specific ones come first.
KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("ID", re.compile(r"\b[A-Z]{2}\d{7}\b")),
    ("MEMBER", re.compile(r"\bM-\d{6}\b")),
    ("ACCOUNT", re.compile(r"\bA-\d{6}\b")),
    ("APPLICATION", re.compile(r"\bAPP-\d{5}\b")),
    # Phones before bare numbers, so the leading "+" is masked with the digits
    # rather than left behind as a hint about what was removed.
    ("PHONE", re.compile(r"\+\d[\d\s-]{7,16}\d\b")),
    # A run of digits long enough to be an account or card number, allowing
    # the spaces and dashes people write them with.
    ("NUMBER", re.compile(r"\b\d[\d\s-]{11,22}\d\b")),
)


@dataclass
class MaskMap:
    """The placeholders used in one request, and what they stand for.

    Per request, never global: a placeholder that meant the same person across
    requests would itself be an identifier.
    """

    forward: dict[str, str] = field(default_factory=dict)
    reverse: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def token_for(self, kind: str, value: str) -> str:
        if value in self.forward:
            return self.forward[value]
        self.counts[kind] = self.counts.get(kind, 0) + 1
        token = _PLACEHOLDER.format(kind=kind, index=self.counts[kind])
        self.forward[value] = token
        self.reverse[token] = value
        return token

    @property
    def masked(self) -> int:
        return len(self.forward)

    def as_dict(self) -> dict[str, Any]:
        """What was masked, without saying what it was."""
        return {"masked_fields": self.masked, "kinds": dict(sorted(self.counts.items()))}


def _mask_known_values(text: str, values: Mapping[str, Iterable[str]], mapping: MaskMap) -> str:
    """Replace values the caller supplied, longest first.

    Longest first so that masking a full name does not leave its surname
    behind as a separate, still-identifying fragment.
    """
    pairs = [(kind, str(value)) for kind, items in values.items() for value in items if str(value).strip()]
    for kind, value in sorted(pairs, key=lambda pair: -len(pair[1])):
        if value not in text:
            continue
        text = text.replace(value, mapping.token_for(kind.upper(), value))
    return text


def mask_text(
    text: str,
    *,
    mapping: MaskMap | None = None,
    known: Mapping[str, Iterable[str]] | None = None,
) -> tuple[str, MaskMap]:
    """Replace identifying values with stable placeholders."""
    mapping = mapping or MaskMap()
    if known:
        text = _mask_known_values(text, known, mapping)
    for kind, pattern in KINDS:

        def replace(match: re.Match[str], k: str = kind) -> str:
            return mapping.token_for(k, match.group(0))

        text = pattern.sub(replace, text)
    return text, mapping


def mask_payload(
    payload: Any,
    *,
    mapping: MaskMap | None = None,
    known: Mapping[str, Iterable[str]] | None = None,
) -> tuple[Any, MaskMap]:
    """Mask every string anywhere in a structure, sharing one placeholder map."""
    mapping = mapping or MaskMap()
    if isinstance(payload, str):
        return mask_text(payload, mapping=mapping, known=known)[0], mapping
    if isinstance(payload, Mapping):
        out = {}
        for key, value in payload.items():
            out[key], mapping = mask_payload(value, mapping=mapping, known=known)
        return out, mapping
    if isinstance(payload, list):
        items = []
        for value in payload:
            masked, mapping = mask_payload(value, mapping=mapping, known=known)
            items.append(masked)
        return items, mapping
    return payload, mapping


def unmask_text(text: str, mapping: MaskMap) -> str:
    """Put the real values back into a model's answer."""
    if not mapping.reverse:
        return text

    def restore(match: re.Match[str]) -> str:
        return mapping.reverse.get(match.group(0), match.group(0))

    return _PLACEHOLDER_PATTERN.sub(restore, text)


def unmask_payload(payload: Any, mapping: MaskMap) -> Any:
    if isinstance(payload, str):
        return unmask_text(payload, mapping)
    if isinstance(payload, Mapping):
        return {key: unmask_payload(value, mapping) for key, value in payload.items()}
    if isinstance(payload, list):
        return [unmask_payload(value, mapping) for value in payload]
    return payload


def leaked(text: str, mapping: MaskMap) -> list[str]:
    """Any masked value that appears unmasked in the text.

    Used to check the gateway's own work: if a value was worth masking on the
    way out, it must not be sitting in the prompt that was actually sent.
    """
    return sorted(value for value in mapping.reverse.values() if value in text)

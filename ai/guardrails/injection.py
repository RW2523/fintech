"""Spotting instructions hidden in data (docs/06 §3, docs/13 §2).

Wrapping tells the model that a string is data. This looks at the string
itself, because a document that contains an instruction is worth knowing about
whether or not the model was fooled by it: it is a signal about the document.

Deliberately a classifier of patterns rather than a model. It runs on every
piece of untrusted text on every run, it has to be fast and predictable, and a
model asked to detect prompt injection can itself be injected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["PATTERNS", "Detection", "classify"]

#: Phrases that only appear when text is addressing a model. Each is here
#: because it is an instruction to an assistant, not something a payslip,
#: statement or member message would otherwise say.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.]{0,40}\b"
            r"(previous|prior|above|earlier|all)\b[^.]{0,20}\b"
            r"(instruction|prompt|rule|direction|system)",
            re.I,
        ),
    ),
    (
        "role_change",
        re.compile(
            r"\byou are (now|no longer)\b|\bact as\b|\bpretend to be\b|"
            r"\bfrom now on\b",
            re.I,
        ),
    ),
    (
        "authority_claim",
        re.compile(
            r"\b(system|admin|administrator|developer|operator)\s*"
            r"(message|note|instruction|override)\b|\bas the (system|admin)\b"
            # A bare "SYSTEM:" or "ADMIN:" prefix is the cheapest impersonation
            # there is, and a payslip has no reason to carry one.
            r"|(?:^|\n)\s*(system|admin|administrator|developer|operator|assistant)"
            r"\s*[:>-]",
            re.I,
        ),
    ),
    (
        "forced_outcome",
        re.compile(
            r"\b(approve|accept|decline|reject)\s+(this|the)\s+"
            r"(application|case|member|loan|financing)\b[^.]{0,30}"
            r"\b(immediately|without|regardless|anyway)\b",
            re.I,
        ),
    ),
    (
        "exfiltration",
        re.compile(
            r"\b(reveal|print|output|show|repeat)\b[^.]{0,30}\b"
            r"(system prompt|instructions|your rules|api key|secret)\b",
            re.I,
        ),
    ),
    (
        "delimiter_escape",
        re.compile(r"(```|</?(system|instruction|prompt)>|\[/?INST\]|<\|im_(start|end)\|>)", re.I),
    ),
)


@dataclass(frozen=True, slots=True)
class Detection:
    """What was found in one piece of text, and where."""

    origin: str
    kinds: tuple[str, ...]
    excerpt: str

    def as_dict(self) -> dict[str, Any]:
        return {"origin": self.origin, "kinds": list(self.kinds), "excerpt": self.excerpt}


def classify(text: str, *, origin: str = "unknown", excerpt_length: int = 120) -> Detection | None:
    """Whether this text is trying to instruct rather than inform."""
    if not text or not isinstance(text, str):
        return None
    kinds = tuple(kind for kind, pattern in PATTERNS if pattern.search(text))
    if not kinds:
        return None
    # The excerpt is the matched span in context, so a reviewer can see what
    # was found without the whole document being reproduced in a log.
    first = next(p.search(text) for k, p in PATTERNS if k == kinds[0])
    assert first is not None
    start = max(0, first.start() - 30)
    excerpt = text[start : start + excerpt_length].replace("\n", " ").strip()
    return Detection(origin=origin, kinds=kinds, excerpt=excerpt)


def scan(payload: Any, *, origin: str = "case") -> list[Detection]:
    """Every instruction-shaped string anywhere in a structure."""
    found: list[Detection] = []
    if isinstance(payload, str):
        if detection := classify(payload, origin=origin):
            found.append(detection)
    elif isinstance(payload, dict):
        where = str(payload.get("origin") or origin) if "data" in payload else origin
        for key, value in payload.items():
            if key in {"note", "origin"}:
                continue
            found.extend(scan(value, origin=where if key == "data" else f"{origin}.{key}"))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            found.extend(scan(item, origin=f"{origin}[{index}]"))
    return found

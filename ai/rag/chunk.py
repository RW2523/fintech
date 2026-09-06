"""Splitting the policy corpus into citable clauses (docs/06 §7).

An agent cites a clause, so a clause is the unit. Splitting by token count
would cut a rule in half and produce two chunks neither of which is the rule;
splitting by heading gives chunks that correspond to something a person can
look up, and the clause id is already the heading.

A clause longer than the limit is split, but only at paragraph boundaries and
with the heading repeated, so every piece still says which clause it belongs
to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = ["MAX_TOKENS", "Clause", "chunk_corpus", "chunk_document"]

#: docs/06 §7 — clauses of at most 600 tokens. Counted as words times a
#: constant rather than with a tokeniser: the limit exists to keep a chunk
#: readable in a prompt, and being approximately right is enough for that.
MAX_TOKENS = 600
_TOKENS_PER_WORD = 1.35

_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)
_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.M)
#: A clause id is what the decision engine evaluates. The prefixes are two to
#: four letters, because the routing rules are `RT-` and the rest are three:
#: requiring three silently dropped every routing clause from the index.
_CLAUSE_ID = re.compile(r"^([A-Z]{2,4}-[A-Z0-9]{2,})\b")


def estimate_tokens(text: str) -> int:
    return int(len(text.split()) * _TOKENS_PER_WORD)


@dataclass(frozen=True, slots=True)
class Clause:
    """One retrievable piece of policy."""

    clause_id: str
    doc: str
    product: str
    version: str
    heading: str
    text: str
    part: int = 1
    parts: int = 1

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)

    @property
    def key(self) -> str:
        """Unique across the index, including a split clause's pieces.

        The version is part of the key. Two versions of a product sheet carry
        the same clause ids, and without it the newer row overwrote the older
        on upsert: a lookup filtered to the version a case was decided under
        would have returned the text of a version adopted afterwards, which is
        the one thing a policy citation must never do.
        """
        return f"{self.doc}@{self.version}:{self.clause_id}" + (f"#{self.part}" if self.parts > 1 else "")

    def as_row(self) -> dict[str, Any]:
        return {
            "clause_id": self.clause_id,
            "key": self.key,
            "doc": self.doc,
            "product": self.product,
            "version": self.version,
            "heading": self.heading,
            "text": self.text,
            "part": self.part,
            "parts": self.parts,
        }


def _split_long(text: str) -> list[str]:
    """Break an over-long clause at paragraph boundaries, never mid-sentence."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    pieces: list[str] = []
    current: list[str] = []
    for paragraph in paragraphs:
        candidate = [*current, paragraph]
        if current and estimate_tokens("\n\n".join(candidate)) > MAX_TOKENS:
            pieces.append("\n\n".join(current))
            current = [paragraph]
        else:
            current = candidate
    if current:
        pieces.append("\n\n".join(current))
    return pieces or [text]


def chunk_document(path: Path) -> list[Clause]:
    """Every clause in one corpus file."""
    raw = path.read_text()
    match = _FRONT_MATTER.match(raw)
    meta: dict[str, Any] = yaml.safe_load(match.group(1)) if match else {}
    body = raw[match.end() :] if match else raw
    doc = str(meta.get("doc") or path.stem)
    product = str(meta.get("product") or "ALL")
    version = str(meta.get("version") or "")

    headings = list(_HEADING.finditer(body))
    clauses: list[Clause] = []
    for index, heading in enumerate(headings):
        title = heading.group(2).strip()
        identifier = _CLAUSE_ID.match(title)
        if identifier is None:
            # A section heading, not a clause. Its prose belongs to the clauses
            # under it rather than being retrievable on its own.
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        text = body[heading.start() : end].strip()
        pieces = _split_long(text) if estimate_tokens(text) > MAX_TOKENS else [text]
        for part, piece in enumerate(pieces, start=1):
            # The heading is repeated on every piece, so a retrieved fragment
            # still says which clause it is part of.
            content = piece if piece.startswith("#") else f"{heading.group(0)}\n\n{piece}"
            clauses.append(
                Clause(
                    clause_id=identifier.group(1),
                    doc=doc,
                    product=product,
                    version=version,
                    heading=title,
                    text=content,
                    part=part,
                    parts=len(pieces),
                )
            )
    return clauses


def chunk_corpus(directory: Path) -> list[Clause]:
    """Every clause in the corpus, in a stable order."""
    clauses: list[Clause] = []
    for path in sorted(directory.glob("*.md")):
        clauses.extend(chunk_document(path))
    return clauses

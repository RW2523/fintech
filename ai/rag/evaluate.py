"""Scoring retrieval against the corpus it indexed (docs/00 T-041).

The acceptance is that a question naming a clause returns that clause in the
top three. It is a low bar deliberately: an agent that cites `ELG-02` must be
able to find `ELG-02`, and a retrieval layer that cannot do that will
confidently cite the wrong rule.

Two question forms are scored. The bare clause id is the acceptance. The
question phrased in the officer's own words is scored beside it, because that
is what a copilot will actually be asked and it is worth knowing where it
stands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai.rag.chunk import chunk_corpus
from ai.rag.index import retrieve

__all__ = ["RetrievalReport", "evaluate_retrieval"]

#: docs/00 T-041 — the correct clause in the top three, at least nine times in ten.
TOP_K = 3
TARGET = 0.90


@dataclass
class RetrievalReport:
    asked: int = 0
    found: int = 0
    prose_asked: int = 0
    prose_found: int = 0
    misses: list[dict[str, Any]] = field(default_factory=list)
    retrieval: str = "unknown"

    @property
    def rate(self) -> float:
        return self.found / self.asked if self.asked else 0.0

    @property
    def prose_rate(self) -> float:
        return self.prose_found / self.prose_asked if self.prose_asked else 0.0

    @property
    def meets_target(self) -> bool:
        return self.rate >= TARGET

    def as_dict(self) -> dict[str, Any]:
        return {
            "clause_id_questions": self.asked,
            "clause_id_found_in_top_3": self.found,
            "clause_id_rate": round(self.rate, 4),
            "prose_questions": self.prose_asked,
            "prose_found_in_top_3": self.prose_found,
            "prose_rate": round(self.prose_rate, 4),
            "target": TARGET,
            "top_k": TOP_K,
            "retrieval": self.retrieval,
            "meets_target": self.meets_target,
            "misses": self.misses[:10],
        }


#: Questions an officer would actually type, and the clause that answers them.
#: Written by hand because a generated question is a paraphrase of the clause
#: and would measure the paraphraser rather than the retrieval.
PROSE_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("what is the minimum membership tenure for financing?", "ELG-02"),
    ("does the member need their identity verified?", "ELG-03"),
    ("which documents must be on file before a decision?", "DOC-01"),
    ("what happens if a document cannot be read clearly?", "DOC-04"),
    ("what is the maximum debt service ratio?", "AFF-01"),
    ("how much total exposure can one member have?", "EXP-01"),
    ("who approves an exception to policy?", "AUT-EX"),
    ("how much can a credit officer approve on their own?", "AUT-01"),
    ("when does a case go to compliance review?", "RT-01"),
    ("can a guarantor be approached before the member?", "COL-05"),
    ("what is offered to a member who cannot pay in full?", "COL-03"),
    ("does a hardship arrangement count as arrears?", "HRD-04"),
    ("what evidence is needed to show hardship?", "HRD-02"),
    ("when is a reminder sent after a missed payment?", "COL-01"),
)


async def evaluate_retrieval(corpus: Path, *, gateway_url: str | None = None) -> RetrievalReport:
    report = RetrievalReport()
    clause_ids = sorted({c.clause_id for c in chunk_corpus(corpus)})

    for clause_id in clause_ids:
        hits = await retrieve(clause_id, limit=TOP_K, gateway_url=gateway_url)
        report.asked += 1
        if any(hit.clause_id == clause_id for hit in hits):
            report.found += 1
        else:
            report.misses.append(
                {"question": clause_id, "expected": clause_id, "returned": [h.clause_id for h in hits]}
            )
        if report.retrieval == "unknown" and hits:
            report.retrieval = "hybrid" if any(h.vector for h in hits) else "lexical"

    for question, expected in PROSE_QUESTIONS:
        hits = await retrieve(question, limit=TOP_K, gateway_url=gateway_url)
        report.prose_asked += 1
        if any(hit.clause_id == expected for hit in hits):
            report.prose_found += 1
        else:
            report.misses.append(
                {"question": question, "expected": expected, "returned": [h.clause_id for h in hits]}
            )
    return report

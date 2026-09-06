"""Generated names (docs/10 preamble).

Names are built from syllable tables so no real person's name can appear. The
UI shows the member id token rather than the name; the name exists only so
generated documents look like documents.
"""

from __future__ import annotations

import numpy as np

__all__ = ["employer_name", "person_name"]

_FIRST = (
    "ka",
    "me",
    "ri",
    "so",
    "tu",
    "na",
    "le",
    "vi",
    "ho",
    "za",
    "de",
    "mi",
    "ru",
    "sa",
    "te",
    "ilo",
    "ora",
    "esa",
    "uma",
    "ave",
)
_MIDDLE = (
    "ran",
    "sel",
    "mor",
    "tal",
    "vin",
    "dor",
    "kes",
    "lam",
    "nur",
    "pel",
    "rin",
    "sath",
    "tem",
    "vor",
    "wen",
)
_LAST = ("a", "e", "i", "o", "u", "an", "en", "in", "on", "as", "es", "is", "or")

_EMPLOYER_HEAD = (
    "Northern",
    "Central",
    "Coastal",
    "Highland",
    "Riverside",
    "Eastern",
    "Western",
    "Southern",
    "Union",
    "Meridian",
    "Summit",
    "Harbour",
)
_EMPLOYER_TAIL = (
    "Utilities",
    "Transit",
    "Health Trust",
    "Academy",
    "Works",
    "Cooperative",
    "Logistics",
    "Mills",
    "Growers",
    "Retail Group",
    "Authority",
    "Institute",
)


def person_name(rng: np.random.Generator) -> str:
    given = str(rng.choice(_FIRST)) + str(rng.choice(_LAST))
    family = str(rng.choice(_FIRST)) + str(rng.choice(_MIDDLE)) + str(rng.choice(_LAST))
    return f"{given.capitalize()} {family.capitalize()}"


def employer_name(rng: np.random.Generator, index: int) -> str:
    head = str(rng.choice(_EMPLOYER_HEAD))
    tail = str(rng.choice(_EMPLOYER_TAIL))
    return f"{head} {tail} {index:03d}"

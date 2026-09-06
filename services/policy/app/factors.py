"""Decision Factor scoring (docs/05 §4).

The formulas live in `cio_dff` because more than one service needs them. The
risk service produces the CONDUCT score and the fraud service the INTEGRITY
score, and both must use the same arithmetic the policy engine consumes: two
implementations of one formula is two chances for a decision to be
irreproducible. This module re-exports them so the policy code that predates
the move reads unchanged.
"""

from __future__ import annotations

from cio_dff.factors import (
    FAMILIES,
    FAMILY_TOOLS,
    SEVERITY_POINTS,
    FactorScore,
    capacity_from_affordability,
    commitment_score,
    conditions_score,
    conduct_score,
    integrity_score,
    score_family,
)

__all__ = [
    "FAMILIES",
    "FAMILY_TOOLS",
    "SEVERITY_POINTS",
    "FactorScore",
    "capacity_from_affordability",
    "commitment_score",
    "conditions_score",
    "conduct_score",
    "integrity_score",
    "score_family",
]

"""From model drivers to words a person is allowed to hear (docs/07 §2.2).

A driver is a number: this feature moved the log-odds by this much. A reason
code is an approved sentence. The map between them is a file under review, not
a judgement the model makes, so a member can never be told something the
reason-code vocabulary has not sanctioned.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent

#: Drivers below this share of the total absolute movement are noise dressed
#: as an explanation, and listing them would pad a reason with nothing.
MIN_SHARE = 0.02

TOP_DRIVERS = 5


@lru_cache(maxsize=1)
def reason_map() -> dict[str, dict[str, str]]:
    loaded = yaml.safe_load((HERE / "reason_map.yaml").read_text())
    return loaded["features"]


@lru_cache(maxsize=1)
def monotone_table() -> dict[str, int]:
    loaded = yaml.safe_load((HERE / "monotone.yaml").read_text())
    return {name: int(value) for name, value in loaded["features"].items()}


@dataclass(frozen=True, slots=True)
class Driver:
    """One feature's contribution to one decision."""

    feature: str
    value: Any
    contribution: float
    direction: str
    share: float
    reason_code: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "value": self.value,
            "contribution": round(self.contribution, 5),
            "direction": self.direction,
            "share": round(self.share, 4),
            "reason_code": self.reason_code,
        }


def drivers_for(contributions: pd.Series, values: pd.Series, *, top: int = TOP_DRIVERS) -> list[Driver]:
    """The features that moved this case, largest movement first."""
    total = float(np.abs(contributions).sum())
    if total <= 0:
        return []
    mapping = reason_map()
    ranked = contributions.reindex(np.abs(contributions).sort_values(ascending=False).index)

    out: list[Driver] = []
    for feature, contribution in ranked.items():
        share = abs(float(contribution)) / total
        if share < MIN_SHARE or len(out) >= top:
            break
        direction = "ADVERSE" if contribution > 0 else "FAVOURABLE"
        code = mapping.get(str(feature), {}).get(direction.lower())
        raw = values.get(feature)
        if isinstance(raw, float) and np.isnan(raw):
            raw = None
        out.append(
            Driver(
                feature=str(feature),
                value=raw,
                contribution=float(contribution),
                direction=direction,
                share=share,
                reason_code=code,
            )
        )
    return out


def reason_codes(drivers: list[Driver], *, limit: int = TOP_DRIVERS) -> list[str]:
    """Approved codes for these drivers, strongest first, no repeats."""
    seen: list[str] = []
    for driver in drivers:
        if driver.reason_code and driver.reason_code not in seen:
            seen.append(driver.reason_code)
        if len(seen) >= limit:
            break
    return seen

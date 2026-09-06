"""Reading the LMI configuration (docs/07 §4.3).

The change-point parameters live in a file rather than in the code because
they were measured, and because an institution that finds its own book noisier
than this one has to be able to move them without a release.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.changepoint import CONFIRMATION_WINDOW_DAYS, SignalConfig
from cio_common.assets import asset_root

__all__ = ["LmiConfig", "config_path", "load_config"]


def config_path() -> Path:
    """Where lmi.yaml lives, in a checkout or in the image."""
    import os

    if override := os.environ.get("LMI_CONFIG"):
        return Path(override)
    return asset_root("config", "lmi.yaml", start=Path(__file__)) / "lmi.yaml"


class LmiConfig:
    """The signals, their parameters, and what was measured."""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    @property
    def version(self) -> str:
        return str(self._body.get("version", "unknown"))

    def signal(self, name: str) -> SignalConfig:
        """One signal's parameters, or the documented defaults.

        A signal nobody configured gets the spec's k=0.5, h=4.0 rather than
        nothing: an unconfigured signal should still be watched, and silently
        not watching it is the failure mode worth avoiding.
        """
        entry = dict((self._body.get("signals") or {}).get(name) or {})
        return SignalConfig(
            signal=name,
            drift=float(entry.get("drift", 0.5)),
            threshold=float(entry.get("threshold", 4.0)),
            direction=str(entry.get("direction", "up")),
        )

    def min_scale(self, name: str) -> float:
        entry = dict((self._body.get("signals") or {}).get(name) or {})
        return float(entry.get("min_scale", 0.0))

    @property
    def signals(self) -> list[str]:
        return sorted((self._body.get("signals") or {}).keys())

    @property
    def confirmation_window_days(self) -> int:
        block = self._body.get("confirmation") or {}
        return int(block.get("window_days", CONFIRMATION_WINDOW_DAYS))

    @property
    def confirmation_observations(self) -> int:
        block = self._body.get("confirmation") or {}
        return int(block.get("observations", 3))

    @property
    def anomaly(self) -> dict[str, Any]:
        return dict(self._body.get("anomaly") or {})

    @property
    def measured(self) -> dict[str, Any]:
        """What the parameters were measured to do, so a later run can be
        compared with a number rather than with somebody's memory."""
        return dict(self._body.get("measured") or {})


@lru_cache(maxsize=1)
def load_config() -> LmiConfig:
    return LmiConfig(yaml.safe_load(config_path().read_text()) or {})

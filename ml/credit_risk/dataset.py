"""Building the origination training frame (docs/07 §2.2).

One row per account, with the features as they stood the day the account was
opened and the outcome that followed. The features come from the same
`compute_features` the risk service calls at decision time, so there is no
second implementation to drift: whatever the model was trained on is what it
will be served.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]

#: The feature service owns the computation and its module is named `app`, as
#: every service's is. Training imports it directly rather than calling the
#: service over HTTP, because a training set built by different code from the
#: serving path is the classic way a model silently degrades in production.
_FEATURE_SERVICE = ROOT / "services" / "feature"
if str(_FEATURE_SERVICE) not in sys.path:
    sys.path.insert(0, str(_FEATURE_SERVICE))

#: Months of performance that must be observable before an account can be
#: labelled. docs/07 §2.2 sets the label as `late90` within 12 months of
#: origination, so an account opened less than 12 months before the end of the
#: outcome table has not had the chance to show the outcome being modelled.
PERFORMANCE_MONTHS = 12

#: docs/07 §2.2 splits by origination month: train ≤ 14, validation 15-18,
#: test 19-24 of a 24-month window. Held as proportions so the split survives
#: a population regenerated over a different span.
TRAIN_SHARE = 14 / 24
VALID_SHARE = 4 / 24

LABEL = "late90_12m"

#: How far before the opening date the application is taken to have been made.
APPLICATION_LAG = timedelta(days=1)

#: Features an origination model cannot use, because the historical record has
#: no counterpart for them. `doc_min_conf` and `contact_change_days` come from
#: a live application's documents and contact history, and are missing for
#: every account already on the books; `findings_max_severity` and
#: `application_count_12m` are constant for the same reason. They stay in the
#: registry because the policy engine and the fraud service do use them at
#: decision time. Measured over this population: 100%, 100%, one level, one
#: level. Training on a column that is always the same value teaches nothing
#: and hides the fact that the column was never populated.
APPLICATION_ONLY = (
    "doc_min_conf",
    "contact_change_days",
    "findings_max_severity",
    "application_count_12m",
)


@dataclass(frozen=True, slots=True)
class Frame:
    """A labelled training frame and the splits taken from it."""

    rows: pd.DataFrame
    feature_names: list[str]
    cohorts: dict[str, list[str]]
    censored: int
    as_of: datetime

    def split(self, name: str) -> pd.DataFrame:
        return self.rows[self.rows["split"] == name].reset_index(drop=True)

    @property
    def summary(self) -> dict[str, Any]:
        counts = {}
        for name in ("train", "valid", "test"):
            part = self.split(name)
            counts[name] = {
                "cohorts": self.cohorts[name],
                "n": len(part),
                "events": int(part[LABEL].sum()),
                "rate": round(float(part[LABEL].mean()), 5) if len(part) else None,
            }
        return {
            "label": LABEL,
            "performance_months": PERFORMANCE_MONTHS,
            "censored_excluded": self.censored,
            "splits": counts,
        }


_ELIGIBLE = text("""
    WITH horizon AS (SELECT max(month) AS last_month FROM core.outcome)
    SELECT a.account_id,
           a.member_id,
           a.opened_at,
           a.instalment,
           a.product_code,
           date_trunc('month', a.opened_at)::date AS cohort,
           COALESCE(bool_or(o.late90) FILTER (
               WHERE o.month < a.opened_at + make_interval(months => :months)), false) AS label
      FROM core.account a
      CROSS JOIN horizon h
      LEFT JOIN core.outcome o ON o.account_id = a.account_id
     WHERE date_trunc('month', a.opened_at)::date
           + make_interval(months => :months) <= h.last_month + interval '1 month'
     GROUP BY a.account_id, a.member_id, a.opened_at, a.instalment, a.product_code
     ORDER BY a.opened_at, a.account_id
""")

_CENSORED = text("""
    WITH horizon AS (SELECT max(month) AS last_month FROM core.outcome)
    SELECT count(*) FROM core.account a CROSS JOIN horizon h
     WHERE date_trunc('month', a.opened_at)::date
           + make_interval(months => :months) > h.last_month + interval '1 month'
""")


def _assign_splits(cohorts: list[str]) -> dict[str, list[str]]:
    """Cut the origination window into three consecutive blocks.

    Consecutive and never shuffled: a model validated on months it was trained
    on would look far better than it is.
    """
    total = len(cohorts)
    n_train = max(1, round(total * TRAIN_SHARE))
    n_valid = max(1, round(total * VALID_SHARE))
    if n_train + n_valid >= total:
        raise ValueError(f"{total} origination cohorts is too few to split three ways")
    return {
        "train": cohorts[:n_train],
        "valid": cohorts[n_train : n_train + n_valid],
        "test": cohorts[n_train + n_valid :],
    }


async def build(*, limit: int | None = None, progress: bool = True) -> Frame:
    """Compute features at origination for every labellable account."""
    from app.compute import compute_features
    from app.db import dispose, session
    from app.registry import names

    feature_names = list(names())
    records: list[dict[str, Any]] = []

    async with session() as db:
        censored = int((await db.execute(_CENSORED, {"months": PERFORMANCE_MONTHS})).scalar_one())
        accounts = (await db.execute(_ELIGIBLE, {"months": PERFORMANCE_MONTHS})).mappings().all()
        if limit:
            accounts = accounts[:limit]

        for index, account in enumerate(accounts, start=1):
            opened = account["opened_at"]
            # The day before it opened, not the day itself: on the opening day
            # the new facility is already on the member's record, and a model
            # trained on that would be reading the answer to the question it is
            # being asked. At application time the facility does not exist yet,
            # and its instalment is passed separately as the proposal.
            as_of = datetime(opened.year, opened.month, opened.day, tzinfo=UTC) - APPLICATION_LAG
            snapshot = await compute_features(
                db,
                account["member_id"],
                as_of=as_of,
                account_id=account["account_id"],
                proposed_instalment=float(account["instalment"]),
            )
            row: dict[str, Any] = {
                "account_id": account["account_id"],
                "member_id": account["member_id"],
                "opened_at": opened,
                "cohort": account["cohort"].isoformat(),
                "product_code": account["product_code"],
                "instalment": float(account["instalment"]),
                LABEL: int(bool(account["label"])),
                "inputs_digest": snapshot.inputs_digest,
            }
            row.update(snapshot.as_dict())
            records.append(row)
            if progress and index % 500 == 0:
                print(f"  ... {index}/{len(accounts)} accounts", flush=True)

    await dispose()

    rows = pd.DataFrame.from_records(records)
    cohorts = _assign_splits(sorted(rows["cohort"].unique()))
    lookup = {cohort: name for name, block in cohorts.items() for cohort in block}
    rows["split"] = rows["cohort"].map(lookup)
    return Frame(
        rows=rows, feature_names=feature_names, cohorts=cohorts, censored=censored, as_of=datetime.now(UTC)
    )


def build_sync(**kwargs: Any) -> Frame:
    return asyncio.run(build(**kwargs))

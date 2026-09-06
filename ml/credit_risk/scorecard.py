"""The champion scorecard: weight-of-evidence bins and a constrained logistic.

A scorecard is chosen over a free-form model because an origination decision
has to be explainable to the member it affects and defensible to a regulator.
Two properties do that work:

* every feature is binned, so the relationship to risk is a small table a
  person can read rather than a curve;
* the direction of every relationship is fixed by policy, not by the sample,
  so a member with more arrears can never score better for having them.

The weight of evidence of a bin is ``ln(P(bin | default) / P(bin | good))``.
It is positive where defaults concentrate, so after binning every coefficient
must be non-negative and the constraint is the same for every feature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.isotonic import IsotonicRegression

#: Pseudo-observations of the pooled default rate mixed into each bin before
#: the log, in units of accounts. Without any, a bin holding no defaults gives
#: an infinite weight and one empty cell decides the model. Mixed in as a fixed
#: count rather than in proportion to bin size it does something worse: a bin of
#: two hundred and a bin of thirteen hundred are pulled by different amounts,
#: and two bins whose default rates are correctly ordered can come out of the
#: log in the wrong order. Shrinking each bin's *rate* toward the pooled rate
#: moves a small bin a long way and a large bin barely at all, which is the
#: behaviour wanted.
_SMOOTHING = 10.0

#: A bin thinner than this is merged into its neighbour. Five defaults is
#: already a weak basis for a weight; fewer is noise.
MIN_BIN_SHARE = 0.05

#: Conventional scorecard reading of an information value: below 0.02 a
#: characteristic is not predictive, 0.02-0.1 is weak, 0.1-0.3 medium. The
#: floor sits at the top of "not predictive" because, measured on a few
#: thousand rows across five bins, a column of pure noise reaches about 0.034.
MIN_INFORMATION_VALUE = 0.05

MISSING = "__missing__"
OTHER = "__other__"


@dataclass(frozen=True, slots=True)
class Bin:
    """One row of a scorecard characteristic."""

    label: str
    woe: float
    n: int
    events: int
    lower: float | None = None
    upper: float | None = None
    levels: tuple[str, ...] = ()

    @property
    def event_rate(self) -> float:
        return self.events / self.n if self.n else 0.0


@dataclass
class Characteristic:
    """A binned feature, with the evidence each bin carries."""

    name: str
    kind: str
    monotone: int
    bins: list[Bin]
    information_value: float
    edges: list[float] = field(default_factory=list)
    level_map: dict[str, int] = field(default_factory=dict)

    def transform(self, values: pd.Series) -> np.ndarray:
        """Map raw values to their bin's weight of evidence."""
        weights = np.array([b.woe for b in self.bins], dtype=float)
        return weights[self.assign(values)]

    def assign(self, values: pd.Series) -> np.ndarray:
        """The bin index for each value. Index 0 is always the missing bin."""
        out = np.zeros(len(values), dtype=int)
        if self.kind == "categorical":
            as_text = values.astype("object")
            for position, value in enumerate(as_text):
                if value is None or (isinstance(value, float) and np.isnan(value)):
                    out[position] = 0
                else:
                    out[position] = self.level_map.get(str(value), self.level_map[OTHER])
            return out
        numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
        present = ~np.isnan(numeric)
        # `searchsorted` on the interior edges; bin 0 is reserved for missing.
        out[present] = 1 + np.searchsorted(
            np.asarray(self.edges, dtype=float), numeric[present], side="right"
        )
        # A characteristic that was entirely missing while training has only the
        # missing bin. Scoring must still answer, so a value it never saw falls
        # back to that bin rather than raising at decision time.
        return np.clip(out, 0, len(self.bins) - 1)

    def table(self) -> list[dict[str, Any]]:
        return [
            {
                "bin": b.label,
                "n": b.n,
                "events": b.events,
                "event_rate": round(b.event_rate, 5),
                "woe": round(b.woe, 4),
            }
            for b in self.bins
        ]


def _woe_and_iv(events: np.ndarray, n: np.ndarray) -> tuple[np.ndarray, float]:
    """Weight of evidence per bin, and the characteristic's information value."""
    total_n = float(n.sum())
    if total_n <= 0:
        return np.zeros_like(events, dtype=float), 0.0
    pooled = float(events.sum()) / total_n

    # Each bin's rate is pulled toward the pooled rate by `_SMOOTHING`
    # pseudo-observations, so the pull is large where the evidence is thin.
    shrunk = (events + _SMOOTHING * pooled) / (n + _SMOOTHING)
    bad = shrunk * n
    good = (1.0 - shrunk) * n

    bad_share = bad / max(bad.sum(), 1e-12)
    good_share = good / max(good.sum(), 1e-12)
    with np.errstate(divide="ignore", invalid="ignore"):
        woe = np.log(np.clip(bad_share, 1e-12, None) / np.clip(good_share, 1e-12, None))

    # An empty bin holds no evidence, and it is exactly where an unseen value
    # lands -- a new employer sector, a feature that was always missing while
    # training. Scoring it as anything but neutral would make the first
    # unfamiliar case the riskiest the model has ever seen.
    woe = np.where(n > 0, woe, 0.0)
    iv = float(np.sum(np.where(n > 0, (bad_share - good_share) * woe, 0.0)))
    return woe, iv


def _force_direction(woe: np.ndarray, n: np.ndarray, monotone: int) -> np.ndarray:
    """Make the weights run the way policy says, whatever the sample did.

    Ordering the bins by default rate is not enough: shrinkage and unequal bin
    sizes can still hand two correctly ordered bins weights in the wrong order,
    and a single inversion would let a member score better for a worse value.
    A weighted isotonic pass over the weights themselves closes that, and it is
    applied to the value bins only -- the missing bin has no place on the scale.
    """
    if not monotone or len(woe) < 2:
        return woe
    weights = np.maximum(n.astype(float), 1e-9)
    shaped = IsotonicRegression(increasing=monotone > 0).fit_transform(
        np.arange(len(woe)), woe, sample_weight=weights
    )
    return np.asarray(shaped, dtype=float)


def _merge_small(groups: list[np.ndarray], y: np.ndarray, floor: int) -> list[np.ndarray]:
    """Fold thin bins into the next one along so no weight rests on a handful."""
    merged: list[np.ndarray] = []
    for group in groups:
        if merged and len(group) < floor:
            merged[-1] = np.concatenate([merged[-1], group])
        else:
            merged.append(group)
    while len(merged) > 1 and len(merged[-1]) < floor:
        merged[-2] = np.concatenate([merged[-2], merged.pop()])
    return merged


def fit_numeric(name: str, values: pd.Series, y: np.ndarray, *, monotone: int, bins: int) -> Characteristic:
    """Quantile-bin a numeric feature, then force the policy direction."""
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    missing = np.isnan(numeric)
    present = np.where(~missing)[0]

    edges: list[float] = []
    groups: list[np.ndarray] = []
    if len(present) >= 2 * bins:
        order = present[np.argsort(numeric[present], kind="stable")]
        quantiles = np.unique(np.quantile(numeric[order], np.linspace(0, 1, bins + 1)[1:-1]))
        cut = np.searchsorted(numeric[order], quantiles, side="right")
        groups = [g for g in np.split(order, cut) if len(g)]
        floor = max(int(MIN_BIN_SHARE * len(present)), 1)
        groups = _merge_small(groups, y, floor)
        edges = [float(numeric[g[-1]]) for g in groups[:-1]]
    elif len(present):
        groups = [present]

    if monotone and len(groups) > 2:
        rates = np.array([y[g].mean() for g in groups])  # raw, before shrinkage
        sizes = np.array([len(g) for g in groups], dtype=float)
        shaped = IsotonicRegression(increasing=monotone > 0).fit_transform(
            np.arange(len(groups)), rates, sample_weight=sizes
        )
        # Bins the isotonic fit could not tell apart carry the same weight, so
        # keeping them separate would only invent detail the data cannot support.
        collapsed: list[np.ndarray] = [groups[0]]
        kept_edges: list[float] = []
        for index in range(1, len(groups)):
            if np.isclose(shaped[index], shaped[index - 1]):
                collapsed[-1] = np.concatenate([collapsed[-1], groups[index]])
            else:
                kept_edges.append(edges[index - 1])
                collapsed.append(groups[index])
        groups, edges = collapsed, kept_edges

    counts = np.array([len(np.where(missing)[0])] + [len(g) for g in groups], dtype=float)
    events = np.array([y[missing].sum()] + [y[g].sum() for g in groups], dtype=float)
    woe, iv = _woe_and_iv(events, counts)
    woe = np.concatenate([woe[:1], _force_direction(woe[1:], counts[1:], monotone)])

    table = [Bin(label="missing", woe=float(woe[0]), n=int(counts[0]), events=int(events[0]))]
    bounds = [-np.inf, *edges, np.inf]
    for index in range(len(groups)):
        low, high = bounds[index], bounds[index + 1]
        label = (
            f"<= {high:,.4g}"
            if index == 0
            else f"> {low:,.4g}"
            if index == len(groups) - 1
            else f"({low:,.4g}, {high:,.4g}]"
        )
        table.append(
            Bin(
                label=label,
                woe=float(woe[index + 1]),
                n=int(counts[index + 1]),
                events=int(events[index + 1]),
                lower=None if low == -np.inf else float(low),
                upper=None if high == np.inf else float(high),
            )
        )
    return Characteristic(
        name=name, kind="numeric", monotone=monotone, bins=table, information_value=iv, edges=edges
    )


def fit_categorical(name: str, values: pd.Series, y: np.ndarray, *, min_count: int = 30) -> Characteristic:
    """One bin per level, with thin levels pooled."""
    as_text = values.astype("object")
    missing = as_text.isna().to_numpy()
    counts = as_text[~missing].value_counts()
    kept = [str(level) for level, count in counts.items() if count >= min_count]

    labels = ["missing", *kept, OTHER]
    level_map = {level: index + 1 for index, level in enumerate(kept)}
    level_map[OTHER] = len(kept) + 1

    masks = [missing]
    masks += [(as_text == level).to_numpy() & ~missing for level in kept]
    masks.append(~missing & ~np.isin(as_text.to_numpy(), kept))

    n = np.array([int(m.sum()) for m in masks], dtype=float)
    events = np.array([float(y[m].sum()) for m in masks], dtype=float)
    woe, iv = _woe_and_iv(events, n)
    table = [
        Bin(
            label=label,
            woe=float(woe[index]),
            n=int(n[index]),
            events=int(events[index]),
            levels=(label,) if label not in {"missing", OTHER} else (),
        )
        for index, label in enumerate(labels)
    ]
    return Characteristic(
        name=name, kind="categorical", monotone=0, bins=table, information_value=iv, level_map=level_map
    )


@dataclass
class Binning:
    """The fitted characteristics, in scorecard order."""

    characteristics: dict[str, Characteristic]

    @property
    def names(self) -> list[str]:
        return list(self.characteristics)

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {name: char.transform(frame[name]) for name, char in self.characteristics.items()},
            index=frame.index,
        )

    def information_values(self) -> dict[str, float]:
        return {n: round(c.information_value, 5) for n, c in self.characteristics.items()}


def fit_binning(frame: pd.DataFrame, y: np.ndarray, *, monotone: dict[str, int], bins: int = 5) -> Binning:
    fitted: dict[str, Characteristic] = {}
    for name in frame.columns:
        column = frame[name]
        if column.dtype == object or isinstance(column.dtype, pd.CategoricalDtype):
            fitted[name] = fit_categorical(name, column, y)
        else:
            fitted[name] = fit_numeric(name, column, y, monotone=monotone.get(name, 0), bins=bins)
    return Binning(characteristics=fitted)


def _too_alike(left: pd.Series, right: pd.Series, ceiling: float) -> bool:
    """Whether two characteristics say the same thing.

    A characteristic that never varies has no correlation to measure, and
    numpy would divide by its zero standard deviation to find out.
    """
    if left.std(ddof=0) == 0 or right.std(ddof=0) == 0:
        return False
    correlation = left.corr(right)
    return bool(correlation == correlation and abs(correlation) > ceiling)


def select(
    binning: Binning,
    woe: pd.DataFrame,
    *,
    min_iv: float = MIN_INFORMATION_VALUE,
    max_features: int = 10,
    max_correlation: float = 0.7,
) -> list[str]:
    """Keep the characteristics that carry evidence and are not each other.

    With sixty-odd defaults in the training window, a model given twenty-three
    characteristics would fit the sample rather than the risk. Selection by
    information value with a correlation filter is the ordinary scorecard
    remedy and it is applied on the training split alone.

    The default floor of 0.05 sits in the conventional "weak but usable" band.
    Measured on this sample size, a column of pure noise reaches an information
    value of about 0.034 across five bins, so a lower floor admits noise as a
    characteristic.
    """
    ranked = sorted(binning.information_values().items(), key=lambda kv: -kv[1])
    chosen: list[str] = []
    for name, iv in ranked:
        if iv < min_iv or len(chosen) >= max_features:
            continue
        if any(_too_alike(woe[name], woe[other], max_correlation) for other in chosen):
            continue
        chosen.append(name)
    return chosen


@dataclass
class Scorecard:
    """Binning plus non-negative weights. Predicts a probability of default."""

    binning: Binning
    features: list[str]
    weights: np.ndarray
    intercept: float
    baseline_woe: dict[str, float]

    def score(self, frame: pd.DataFrame) -> np.ndarray:
        """The log-odds."""
        woe = self.binning.transform(frame)[self.features].to_numpy(dtype=float)
        return self.intercept + woe @ self.weights

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.score(frame)))

    def contributions(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Per-feature contribution to the log-odds.

        For a linear model on independent inputs this is exactly the Shapley
        value, so the champion needs no sampling to explain itself: the
        contribution is the weight times the distance from the training mean.
        """
        woe = self.binning.transform(frame)[self.features]
        base = np.array([self.baseline_woe[name] for name in self.features])
        return pd.DataFrame(
            (woe.to_numpy(dtype=float) - base) * self.weights, columns=self.features, index=frame.index
        )

    def table(self) -> dict[str, Any]:
        return {
            "intercept": round(self.intercept, 5),
            "characteristics": [
                {
                    "feature": name,
                    "weight": round(float(weight), 5),
                    "information_value": round(self.binning.characteristics[name].information_value, 5),
                    "monotone": self.binning.characteristics[name].monotone,
                    "bins": self.binning.characteristics[name].table(),
                }
                for name, weight in zip(self.features, self.weights, strict=True)
            ],
        }


def fit_scorecard(
    woe: pd.DataFrame, y: np.ndarray, features: list[str], *, l2: float = 1.0
) -> tuple[np.ndarray, float]:
    """L2 logistic regression with every coefficient held non-negative.

    scikit-learn cannot constrain coefficient signs, so the penalised
    likelihood is minimised directly under bounds. The intercept is free; the
    weights are not, because a negative weight would invert the policy
    direction the binning just established.
    """
    x = woe[features].to_numpy(dtype=float)
    y = np.asarray(y, dtype=float)
    n_features = x.shape[1]

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        intercept, weights = theta[0], theta[1:]
        z = intercept + x @ weights
        # log(1 + exp(z)) computed without overflowing on large z.
        loss = float(np.sum(np.logaddexp(0.0, z) - y * z) + l2 * np.sum(weights**2))
        residual = 1.0 / (1.0 + np.exp(-z)) - y
        grad = np.empty_like(theta)
        grad[0] = residual.sum()
        grad[1:] = x.T @ residual + 2.0 * l2 * weights
        return loss, grad

    start = np.zeros(n_features + 1)
    start[0] = np.log(max(y.mean(), 1e-6) / max(1.0 - y.mean(), 1e-6))
    bounds = [(None, None)] + [(0.0, None)] * n_features
    result = minimize(objective, start, jac=True, method="L-BFGS-B", bounds=bounds, options={"maxiter": 2000})
    return result.x[1:], float(result.x[0])

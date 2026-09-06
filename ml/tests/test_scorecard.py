"""T-031 — the scorecard's binning, constraints and arithmetic."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.credit_risk import scorecard as sc


@pytest.fixture
def sample() -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(7)
    n = 2000
    risk = rng.uniform(0, 1, n)
    frame = pd.DataFrame(
        {
            "worse_is_riskier": risk * 10 + rng.normal(0, 0.5, n),
            "better_is_safer": (1 - risk) * 10 + rng.normal(0, 0.5, n),
            "noise": rng.normal(0, 1, n),
            "sector": rng.choice(["A", "B", "C"], n),
        }
    )
    frame.loc[frame.index[:200], "worse_is_riskier"] = np.nan
    y = rng.binomial(1, np.clip(0.01 + 0.55 * risk**2, 0.001, 0.99))
    return frame, y


MONOTONE = {"worse_is_riskier": 1, "better_is_safer": -1, "noise": 0}


def test_weights_of_evidence_follow_the_declared_direction(sample) -> None:
    """A worse input must never be given a friendlier weight."""
    frame, y = sample
    binning = sc.fit_binning(frame, y, monotone=MONOTONE)

    rising = [b.woe for b in binning.characteristics["worse_is_riskier"].bins[1:]]
    assert rising == sorted(rising), rising

    falling = [b.woe for b in binning.characteristics["better_is_safer"].bins[1:]]
    assert falling == sorted(falling, reverse=True), falling


def test_missing_values_get_their_own_bin(sample) -> None:
    frame, y = sample
    binning = sc.fit_binning(frame, y, monotone=MONOTONE)
    first = binning.characteristics["worse_is_riskier"].bins[0]
    assert first.label == "missing"
    assert first.n == 200


def test_a_signal_carries_more_information_than_noise(sample) -> None:
    frame, y = sample
    binning = sc.fit_binning(frame, y, monotone=MONOTONE)
    values = binning.information_values()
    assert values["worse_is_riskier"] > values["noise"]
    assert values["better_is_safer"] > values["noise"]


def test_selection_drops_the_uninformative(sample) -> None:
    """A column of noise must not become a characteristic.

    The two informative columns here are two views of the same underlying
    risk, so keeping one of them is the correct outcome, not both.
    """
    frame, y = sample
    binning = sc.fit_binning(frame, y, monotone=MONOTONE)
    chosen = sc.select(binning, binning.transform(frame), max_features=10)
    assert {"worse_is_riskier", "better_is_safer"} & set(chosen)
    assert "noise" not in chosen
    assert binning.information_values()["noise"] < sc.MIN_INFORMATION_VALUE


def test_selection_drops_a_duplicate_characteristic(sample) -> None:
    """Two characteristics saying the same thing should not both be counted."""
    frame, y = sample
    frame = frame.assign(copy_of_signal=frame["worse_is_riskier"] * 1.001)
    monotone = {**MONOTONE, "copy_of_signal": 1}
    binning = sc.fit_binning(frame, y, monotone=monotone)
    chosen = sc.select(binning, binning.transform(frame), max_correlation=0.7)
    assert not {"worse_is_riskier", "copy_of_signal"} <= set(chosen)


def test_a_constant_characteristic_does_not_break_selection(sample) -> None:
    frame, y = sample
    frame = frame.assign(always_the_same=1.0)
    binning = sc.fit_binning(frame, y, monotone={**MONOTONE, "always_the_same": 0})
    chosen = sc.select(binning, binning.transform(frame))
    assert "always_the_same" not in chosen


def test_every_coefficient_is_held_non_negative(sample) -> None:
    """A negative weight would invert the direction the binning just fixed."""
    frame, y = sample
    binning = sc.fit_binning(frame, y, monotone=MONOTONE)
    woe = binning.transform(frame)
    features = ["worse_is_riskier", "better_is_safer", "noise"]
    weights, _ = sc.fit_scorecard(woe, y, features)
    assert (weights >= 0).all(), weights


def test_stronger_regularisation_shrinks_the_weights(sample) -> None:
    frame, y = sample
    binning = sc.fit_binning(frame, y, monotone=MONOTONE)
    woe = binning.transform(frame)
    features = ["worse_is_riskier", "better_is_safer"]
    light, _ = sc.fit_scorecard(woe, y, features, l2=0.1)
    heavy, _ = sc.fit_scorecard(woe, y, features, l2=100.0)
    assert heavy.sum() < light.sum()


def test_the_scorecard_ranks_risk(sample) -> None:
    from sklearn.metrics import roc_auc_score

    frame, y = sample
    binning = sc.fit_binning(frame, y, monotone=MONOTONE)
    woe = binning.transform(frame)
    features = sc.select(binning, woe)
    weights, intercept = sc.fit_scorecard(woe, y, features)
    card = sc.Scorecard(binning, features, weights, intercept, {n: float(woe[n].mean()) for n in features})
    assert roc_auc_score(y, card.predict_proba(frame)) > 0.75, card.features


def test_contributions_add_up_to_the_score(sample) -> None:
    """The explanation must account for the number, not approximate it."""
    frame, y = sample
    binning = sc.fit_binning(frame, y, monotone=MONOTONE)
    woe = binning.transform(frame)
    features = sc.select(binning, woe)
    weights, intercept = sc.fit_scorecard(woe, y, features)
    baseline = {n: float(woe[n].mean()) for n in features}
    card = sc.Scorecard(binning, features, weights, intercept, baseline)

    expected = intercept + sum(weights[i] * baseline[n] for i, n in enumerate(features))
    reconstructed = card.contributions(frame).sum(axis=1) + expected
    assert np.allclose(reconstructed.to_numpy(), card.score(frame))


def test_an_empty_bin_carries_no_weight(sample) -> None:
    """Silence is neutral.

    Smoothing alone gives a bin with no observations the weight of a bin made
    entirely of defaults, and an unseen value lands in exactly that bin. The
    first unfamiliar case would then score as the riskiest ever seen.
    """
    frame, y = sample
    binning = sc.fit_binning(frame, y, monotone=MONOTONE)
    for characteristic in binning.characteristics.values():
        for empty in (b for b in characteristic.bins if b.n == 0):
            assert empty.woe == 0.0, (characteristic.name, empty.label, empty.woe)


def test_a_value_never_seen_while_training_still_scores(sample) -> None:
    frame, y = sample
    binning = sc.fit_binning(frame, y, monotone=MONOTONE)
    unseen = pd.DataFrame(
        {
            "worse_is_riskier": [1e9, -1e9, np.nan],
            "better_is_safer": [0.0, 0.0, 0.0],
            "noise": [0.0, 0.0, 0.0],
            "sector": ["ZZZ", None, "A"],
        }
    )
    values = binning.transform(unseen)
    assert values.notna().all().all()

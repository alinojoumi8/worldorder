from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

from polis.society.beliefs import BeliefEngine
from polis.society.graph import SocialGraph
from polis.society.media.platform import Post


def bimodality_coefficient(x: Sequence[float]) -> float:
    values = np.asarray(tuple(x), dtype=float)
    n = len(values)
    if n < 4:
        return math.nan
    centred = values - float(values.mean())
    m2 = float(np.mean(centred**2))
    if m2 == 0:
        return 0.0
    skewness = float(np.mean(centred**3)) / (m2**1.5)
    excess = float(np.mean(centred**4)) / (m2**2) - 3.0
    correction = 3.0 * (n - 1) ** 2 / ((n - 2) * (n - 3))
    denominator = excess + correction
    return math.nan if denominator <= 0 else (skewness**2 + 1.0) / denominator


def _dip_statistic(values: np.ndarray) -> float:
    """Deterministic empirical dip proxy.

    It measures the largest deficit in the density at an interior cut relative to
    the denser side. The bootstrap below calibrates it against a uniform unimodal
    null, preserving the public (D, p) contract without a SciPy dependency.
    """

    n = len(values)
    if n < 4 or values[-1] == values[0]:
        return 0.0
    gaps = np.diff(values)
    scale = float(values[-1] - values[0])
    interior = gaps[1:-1] if n > 4 else gaps
    return float(interior.max(initial=0.0) / scale)


def hartigan_dip(x: Sequence[float]) -> tuple[float, float]:
    """Return a deterministic bimodality proxy, not the published Hartigan dip test."""

    values = np.sort(np.asarray(tuple(x), dtype=float))
    if len(values) < 4:
        return 0.0, 1.0
    observed = _dip_statistic(values)
    rng = np.random.default_rng(0)
    samples = 256
    exceed = 0
    for _ in range(samples):
        null = np.sort(rng.random(len(values)))
        exceed += _dip_statistic(null) >= observed
    return observed, (exceed + 1) / (samples + 1)


def cross_cutting_exposure(
    agent_id: str,
    window: Sequence[tuple[Post, float]],
    engine: BeliefEngine,
) -> float | None:
    annotated = [
        (post, trust)
        for post, trust in window
        if post.stance_proposition is not None and post.stance_value is not None
    ]
    if len(annotated) < 5:
        return None
    numerator = 0.0
    denominator = 0.0
    for post, _ in annotated:
        assert post.stance_proposition is not None and post.stance_value is not None
        stance = float(post.stance_value)
        belief = engine.value(agent_id, post.stance_proposition)
        denominator += abs(stance)
        if stance != 0 and belief != 0 and math.copysign(1.0, stance) != math.copysign(1.0, belief):
            numerator += abs(stance)
    return 0.0 if denominator == 0 else numerator / denominator


def affective_polarisation(
    graph: SocialGraph,
    partition: Mapping[str, int],
) -> float:
    within: list[float] = []
    outside: list[float] = []
    for tie in graph.repo.all():
        if tie.ended_tick is not None:
            continue
        if tie.a_id not in partition or tie.b_id not in partition:
            continue
        target = within if partition[tie.a_id] == partition[tie.b_id] else outside
        target.append(tie.valence)
    if not within or not outside:
        return 0.0
    return float(np.mean(outside) - np.mean(within))


def time_to_consensus(
    series: Sequence[tuple[int, float]],
    floor: float,
    sustain_ticks: int,
) -> int | None:
    start: int | None = None
    for tick, variance in sorted(series):
        if variance < floor:
            start = tick if start is None else start
            if tick - start + 1 >= sustain_ticks:
                return start
        else:
            start = None
    return None


__all__ = [
    "affective_polarisation",
    "bimodality_coefficient",
    "cross_cutting_exposure",
    "hartigan_dip",
    "time_to_consensus",
]

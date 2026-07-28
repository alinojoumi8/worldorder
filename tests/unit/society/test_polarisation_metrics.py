import numpy as np

from polis.society.beliefs_metrics import (
    bimodality_coefficient,
    hartigan_dip,
    time_to_consensus,
)


def test_bimodal_sample_exceeds_unimodal_sample() -> None:
    rng = np.random.default_rng(4)
    unimodal = rng.normal(0.0, 0.25, 400)
    bimodal = np.concatenate((rng.normal(-0.7, 0.08, 200), rng.normal(0.7, 0.08, 200)))
    assert bimodality_coefficient(bimodal) > bimodality_coefficient(unimodal)
    bimodal_dip, bimodal_p = hartigan_dip(bimodal)
    unimodal_dip, _ = hartigan_dip(unimodal)
    assert bimodal_dip > unimodal_dip
    assert 0.0 <= bimodal_p <= 1.0


def test_time_to_consensus_requires_sustained_window() -> None:
    assert time_to_consensus(((1, 0.01), (2, 0.03), (3, 0.01)), 0.02, 2) is None
    assert time_to_consensus(((1, 0.03), (2, 0.01), (3, 0.01)), 0.02, 2) == 2

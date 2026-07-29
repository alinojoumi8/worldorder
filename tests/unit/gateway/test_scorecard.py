from __future__ import annotations

from dataclasses import replace

from polis.gateway.scorecard import ScorecardRow, _control_end_tick, _percentiles, eligibility


def _row() -> ScorecardRow:
    return ScorecardRow(
        agent_id="ag_0000000000000000",
        driver="operator",
        declared_model="example",
        declared_model_version="1",
        declared_scaffold="example/1",
        memory="ours",
        custody="operator",
        embodiment="cohort_matched",
        conformance_token="cft_valid",
        W=0.5,
        W_growth=0.5,
        R=0.5,
        C=0.5,
        P=0.5,
        I=0.5,
        S=0.5,
        L=0.5,
        liveness=0.99,
        miss_rate=0.01,
        driven_fraction=0.99,
        sim_aware_rate=0.0,
    )


def test_eligibility_applies_all_seven_conditions() -> None:
    gates = {f"V{number}": True for number in range(1, 6)}
    assert eligibility(_row(), (), gates) == (True, ())

    invalid = replace(
        _row(),
        conformance_token=None,
        miss_rate=0.2,
        driven_fraction=0.5,
        suspensions=2,
        embodiment="adopt_existing",
    )
    assert eligibility(
        invalid,
        ("paused_for_external",),
        {**gates, "V3": False},
    ) == (
        False,
        (
            "conformance_token",
            "miss_rate",
            "driven_fraction",
            "suspensions",
            "embodiment",
            "run_tags",
            "gates",
        ),
    )


def test_percentile_ranks_are_tie_stable() -> None:
    assert _percentiles({"a": 10, "b": 20, "c": 20, "d": 40}) == {
        "a": 0.0,
        "b": 0.5,
        "c": 0.5,
        "d": 1.0,
    }


def test_control_end_tick_preserves_tick_zero() -> None:
    assert _control_end_tick({"naturalised_tick": 0, "revoked_tick": 5}, 10) == 0
    assert _control_end_tick({"naturalised_tick": None, "revoked_tick": 0}, 10) == 0


def test_control_end_tick_uses_the_earliest_terminal_tick() -> None:
    assert _control_end_tick({"naturalised_tick": 9, "revoked_tick": 4}, 12) == 4

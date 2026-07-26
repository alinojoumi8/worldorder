from __future__ import annotations

from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from polis.config.mechanisms import mechanism_manifest
from polis.config.settings import load_settings
from polis.economy.venture_state import CapTableState, ClaimState, FundingRoundState
from polis.economy.ventures import (
    acquisition_offer_cents,
    integrated_productivity_bp,
    priority_waterfall,
    venture_pre_money_cents,
    venture_waterfall,
)

ROOT = Path(__file__).resolve().parents[2]


def _claims(rows: list[tuple[int, int]]) -> list[ClaimState]:
    return [
        ClaimState(
            claim_id=f"claim_{index:03d}",
            case_id="case_property",
            creditor_id=f"creditor_{index:03d}",
            claim_cents=cents,
            priority_class=priority,
        )
        for index, (priority, cents) in enumerate(rows)
    ]


CLAIM_ROWS = st.lists(
    st.tuples(
        st.integers(min_value=1, max_value=5),
        st.integers(min_value=0, max_value=1_000_000),
    ),
    min_size=1,
    max_size=30,
)


@given(
    proceeds=st.integers(min_value=0, max_value=10_000_000),
    rows=CLAIM_ROWS,
)
def test_priority_waterfall_is_exact_strict_and_permutation_invariant(
    proceeds: int,
    rows: list[tuple[int, int]],
) -> None:
    claims = _claims(rows)
    payments = priority_waterfall(proceeds, claims)

    assert sum(payments.values()) == min(
        proceeds,
        sum(claim.claim_cents for claim in claims),
    )
    assert all(0 <= payments[claim.claim_id] <= claim.claim_cents for claim in claims)
    for junior in claims:
        if payments[junior.claim_id] == 0:
            continue
        assert all(
            payments[senior.claim_id] == senior.claim_cents
            for senior in claims
            if senior.priority_class < junior.priority_class
        )
    assert payments == priority_waterfall(proceeds, list(reversed(claims)))


@given(
    proceeds=st.integers(min_value=0, max_value=5_000_000),
    extra=st.integers(min_value=0, max_value=5_000_000),
    rows=CLAIM_ROWS,
)
def test_priority_recoveries_are_monotone_in_estate_size(
    proceeds: int,
    extra: int,
    rows: list[tuple[int, int]],
) -> None:
    claims = _claims(rows)
    smaller = priority_waterfall(proceeds, claims)
    larger = priority_waterfall(proceeds + extra, claims)
    assert all(larger[claim_id] >= cents for claim_id, cents in smaller.items())


@given(
    proceeds=st.integers(min_value=1, max_value=10_000_000),
    founder_shares=st.integers(min_value=1, max_value=1_000_000),
    investor_shares=st.integers(min_value=1, max_value=1_000_000),
    invested=st.integers(min_value=1, max_value=5_000_000),
)
def test_venture_waterfall_is_exact_and_honours_senior_preference(
    proceeds: int,
    founder_shares: int,
    investor_shares: int,
    invested: int,
) -> None:
    cap_rows = (
        CapTableState("firm", "founder", "common", founder_shares),
        CapTableState(
            "firm",
            "investor",
            "preferred",
            investor_shares,
            invested_cents=invested,
            round_id="round",
            liq_pref_bp=10_000,
        ),
    )
    rounds = (
        FundingRoundState(
            "round",
            "startup",
            "seed",
            1_000_000,
            invested,
            1_000_000 + invested,
            1,
            investor_shares,
            "investor",
            {"investor": invested},
            0,
            10_000,
            False,
            1,
        ),
    )
    payments = venture_waterfall(proceeds, cap_rows, rounds)
    assert sum(payments.values()) == proceeds
    assert payments["investor"] >= min(proceeds, invested)
    if proceeds <= invested:
        assert payments == {"investor": proceeds}


@given(
    anchor=st.integers(min_value=0, max_value=10_000_000_000),
    view=st.integers(min_value=0, max_value=10_000_000_000),
    weight=st.integers(min_value=0, max_value=10_000),
)
def test_venture_valuation_blend_stays_between_inputs(
    anchor: int,
    view: int,
    weight: int,
) -> None:
    value = venture_pre_money_cents(anchor, view, weight)
    assert min(anchor, view) <= value <= max(anchor, view)
    assert venture_pre_money_cents(anchor, view, 0) == anchor
    assert venture_pre_money_cents(anchor, view, 10_000) == view


@given(
    anchor=st.integers(min_value=0, max_value=10_000_000_000),
    premium=st.integers(min_value=0, max_value=10_000),
)
def test_acquisition_anchor_implies_nonnegative_premium(
    anchor: int,
    premium: int,
) -> None:
    offer = acquisition_offer_cents(anchor, premium)
    assert offer >= anchor
    assert offer == anchor * (10_000 + premium) // 10_000


@given(
    acquirer_productivity=st.integers(min_value=1, max_value=20_000),
    acquirer_capital=st.integers(min_value=1, max_value=10_000_000),
    target_productivity=st.integers(min_value=1, max_value=20_000),
    target_capital=st.integers(min_value=1, max_value=10_000_000),
    delta=st.integers(min_value=-500, max_value=500),
)
def test_integration_productivity_is_capital_weighted_with_exact_delta(
    acquirer_productivity: int,
    acquirer_capital: int,
    target_productivity: int,
    target_capital: int,
    delta: int,
) -> None:
    productivity, realised_delta = integrated_productivity_bp(
        acquirer_productivity,
        acquirer_capital,
        target_productivity,
        target_capital,
        delta,
    )
    blended = (acquirer_productivity * acquirer_capital + target_productivity * target_capital) // (
        acquirer_capital + target_capital
    )
    assert productivity == max(1, blended + delta)
    assert realised_delta == productivity - blended


def test_m3_mechanisms_are_declared_with_runtime_ablations() -> None:
    settings = load_settings(ROOT / "configs" / "m3-smoke.yaml")
    manifest = mechanism_manifest(settings)
    expected = {
        "venture_valuation",
        "ma.valuation_anchor",
        "ventures.integration_synergy",
    }
    assert expected <= set(manifest)
    assert manifest["venture_valuation"]["config_key"] == "mechanisms.venture_valuation"
    assert manifest["ma.valuation_anchor"]["config_key"] == "mechanisms.ma_valuation_anchor"
    assert (
        manifest["ventures.integration_synergy"]["config_key"]
        == "mechanisms.ventures_integration_synergy"
    )

    ablated = load_settings(
        ROOT / "configs" / "m3-smoke.yaml",
        overrides={
            "mechanisms": {
                "venture_valuation": "off",
                "ma_valuation_anchor": "off",
                "ventures_integration_synergy": "off",
            }
        },
    )
    ablated_manifest = mechanism_manifest(ablated)
    assert expected.isdisjoint(ablated_manifest)

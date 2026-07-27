from __future__ import annotations

from pathlib import Path

import pytest

from polis.config.errors import RuntimeOverlayError
from polis.config.runtime import LayeredOverlay, StaticOverlay
from polis.config.settings import EconomySettings, ExchangeSettings, load_settings


def test_economy_is_disabled_by_default_for_frozen_m1_runs() -> None:
    configured = load_settings(Path("configs/smoke.yaml"))
    assert not configured.economy.enabled
    assert configured.economy.currency == "POL"


def test_genesis_shares_must_sum_exactly() -> None:
    with pytest.raises(ValueError):
        EconomySettings(household_share_bp=6_999)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bootstrap_listing_day", 0),
        ("zero_intelligence_participation_bp", 10_001),
        ("zero_intelligence_spread_bp", 2_001),
        ("zero_intelligence_max_order_qty", 0),
    ],
)
def test_exchange_calibration_controls_are_bounded(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        ExchangeSettings(**{field: value})


def test_static_and_layered_runtime_overlays_are_typed_and_temporal() -> None:
    configured = load_settings(Path("configs/smoke.yaml"))
    static = StaticOverlay(configured)
    assert static.bp("banking.reserve_ratio_bp", 1) == 1_000
    assert static.cents("labour.minimum_wage_cents", 99) == 0
    assert static.brackets("treasury.tax.income_brackets", 1)[1] == (2_000_000, 1_500)
    with pytest.raises(RuntimeOverlayError):
        static.enact("banking.reserve_ratio_bp", 900, 2, "policy", 1, enacted_tick=1)

    layered = LayeredOverlay(configured)
    layered.enact(
        "banking.reserve_ratio_bp",
        900,
        3,
        "policy",
        10,
        enacted_tick=2,
    )
    assert layered.bp("banking.reserve_ratio_bp", 2) == 1_000
    assert layered.bp("banking.reserve_ratio_bp", 3) == 900

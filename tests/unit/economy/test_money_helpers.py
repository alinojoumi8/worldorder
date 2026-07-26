from __future__ import annotations

import pytest

from polis.economy.money import allocate, bp, bp_ceil, mint, round_to_tick


def test_basis_point_rounding_is_integer_and_directional() -> None:
    assert bp(101, 100) == 1
    assert bp_ceil(101, 100) == 2
    assert bp(0, 9_999) == 0
    assert bp_ceil(0, 9_999) == 0


def test_allocate_is_exact_and_uses_stable_ties() -> None:
    assert allocate(10, (("b", 1), ("a", 1), ("c", 1))) == {
        "a": 4,
        "b": 3,
        "c": 3,
    }
    assert sum(allocate(10_001, (("a", 7), ("b", 3))).values()) == 10_001


@pytest.mark.parametrize(
    ("pool", "weights"),
    [
        (-1, (("a", 1),)),
        (1, ()),
        (1, (("a", 0),)),
        (1, (("a", 1), ("a", 2))),
        (1, (("a", -1), ("b", 2))),
    ],
)
def test_allocate_rejects_invalid_inputs(
    pool: int,
    weights: tuple[tuple[str, int], ...],
) -> None:
    with pytest.raises(ValueError):
        allocate(pool, weights)


def test_mint_and_price_tick_are_deterministic() -> None:
    assert mint("ln", 4120, 3) == "ln_00004120_0003"
    assert round_to_tick(1_259, 10) == 1_250

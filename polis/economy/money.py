from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Context
from typing import Final

MONEY_CTX: Final = Context(prec=28, rounding=ROUND_HALF_EVEN)


def bp(amount_cents: int, rate_bp: int) -> int:
    """Return a basis-point amount rounded down."""
    return (amount_cents * rate_bp) // 10_000


def bp_ceil(amount_cents: int, rate_bp: int) -> int:
    """Return a basis-point amount rounded up."""
    return -((-amount_cents * rate_bp) // 10_000)


def allocate(pool_cents: int, weights: Sequence[tuple[str, int]]) -> dict[str, int]:
    """Split an integer pool exactly with the deterministic largest-remainder rule."""
    if pool_cents < 0:
        raise ValueError("pool_cents must be non-negative")
    if not weights:
        raise ValueError("weights must not be empty")
    if len({claimant_id for claimant_id, _weight in weights}) != len(weights):
        raise ValueError("claimant ids must be unique")
    if any(weight < 0 for _claimant_id, weight in weights):
        raise ValueError("weights must be non-negative")
    total_weight = sum(weight for _claimant_id, weight in weights)
    if total_weight <= 0:
        raise ValueError("weight sum must be positive")

    base = {claimant_id: (pool_cents * weight) // total_weight for claimant_id, weight in weights}
    remainders = {
        claimant_id: (pool_cents * weight) % total_weight for claimant_id, weight in weights
    }
    shortfall = pool_cents - sum(base.values())
    order = sorted(
        weights,
        key=lambda item: (-remainders[item[0]], -item[1], item[0]),
    )
    for claimant_id, _weight in order[:shortfall]:
        base[claimant_id] += 1
    return base


def mint(prefix: str, tick: int, ordinal: int) -> str:
    if not prefix or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789" for character in prefix
    ):
        raise ValueError("prefix must contain lowercase ASCII letters or digits")
    if tick < 0 or ordinal < 0:
        raise ValueError("tick and ordinal must be non-negative")
    return f"{prefix}_{tick:08d}_{ordinal:04d}"


def round_to_tick(cents: int, tick_size_cents: int) -> int:
    """Round a non-negative price down to the configured price tick."""
    if cents < 0:
        raise ValueError("cents must be non-negative")
    if tick_size_cents <= 0:
        raise ValueError("tick_size_cents must be positive")
    return (cents // tick_size_cents) * tick_size_cents

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from polis.economy.money import allocate


@given(
    pool=st.integers(min_value=0, max_value=10**12),
    raw_weights=st.lists(
        st.integers(min_value=1, max_value=10**9),
        min_size=1,
        max_size=30,
    ),
)
def test_largest_remainder_always_allocates_the_exact_pool(
    pool: int,
    raw_weights: list[int],
) -> None:
    weights = tuple((f"owner_{index:03d}", weight) for index, weight in enumerate(raw_weights))
    result = allocate(pool, weights)
    assert sum(result.values()) == pool
    assert all(value >= 0 for value in result.values())

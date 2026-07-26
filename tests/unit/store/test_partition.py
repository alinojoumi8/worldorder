from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from polis.store.engine import StoreError
from polis.store.partition import partition_name, validate_ident


def test_partition_names_are_stable_and_bounded() -> None:
    run_id = UUID("12345678-1234-5678-1234-567812345678")
    assert partition_name("events", run_id) == "ev_12345678123456781234567812345678"
    assert partition_name("events", run_id, 12).endswith("_12")
    assert len(partition_name("events", run_id, 999_999)) <= 63


@pytest.mark.parametrize("value", ["; DROP TABLE events", "Upper", "1starts_wrong", "é", "x" * 64])
def test_identifier_gate_rejects_unsafe_names(value: str) -> None:
    with pytest.raises(StoreError):
        validate_ident(value)


@given(st.text())
def test_accepted_identifiers_match_the_closed_grammar(value: str) -> None:
    try:
        accepted = validate_ident(value)
    except StoreError:
        return
    assert accepted.isascii()
    assert accepted == accepted.lower()
    assert accepted[0].isalpha() or accepted[0] == "_"

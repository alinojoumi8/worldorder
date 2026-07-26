from datetime import datetime
from decimal import Decimal

import pytest

from polis.config.canon import canonical_json, round_floats
from polis.config.errors import ConfigError


def test_canonical_json_is_stable_and_utf8_safe() -> None:
    assert canonical_json({"b": 1, "a": "café"}) == canonical_json({"a": "café", "b": 1})
    assert "café" in canonical_json({"value": "café"})


@pytest.mark.parametrize("value", [datetime(2026, 1, 1), Decimal("1.2"), {1}, b"x"])
def test_canonical_json_rejects_non_primitives(value: object) -> None:
    with pytest.raises(ConfigError):
        canonical_json({"value": value})


def test_round_floats_is_deep_and_idempotent() -> None:
    value = {"x": [1.123456789, 2], "y": (3.9999999,)}
    rounded = round_floats(value)
    assert rounded == {"x": [1.123457, 2], "y": (4.0,)}
    assert round_floats(rounded) == rounded

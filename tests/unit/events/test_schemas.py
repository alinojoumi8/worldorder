from decimal import Decimal

import pytest

from polis.events.kinds import RUN_STARTED
from polis.events.schemas import PayloadSchemaError, validate_payload


def test_required_payload_field_is_enforced() -> None:
    with pytest.raises(PayloadSchemaError, match="seed"):
        validate_payload(RUN_STARTED, {"config_hash": "abc"})


@pytest.mark.parametrize("value", [Decimal("1"), b"x", {1}, float("nan")])
def test_non_json_payload_is_rejected(value: object) -> None:
    with pytest.raises(PayloadSchemaError):
        validate_payload(RUN_STARTED, {"config_hash": value, "seed": 1})

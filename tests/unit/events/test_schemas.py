from decimal import Decimal

import pytest

from polis.events.kinds import POST_ENGAGED, POST_PUBLISHED, RUN_STARTED, range_for
from polis.events.schemas import PayloadSchemaError, validate_payload


def test_required_payload_field_is_enforced() -> None:
    with pytest.raises(PayloadSchemaError, match="seed"):
        validate_payload(RUN_STARTED, {"config_hash": "abc"})


@pytest.mark.parametrize("value", [Decimal("1"), b"x", {1}, float("nan")])
def test_non_json_payload_is_rejected(value: object) -> None:
    with pytest.raises(PayloadSchemaError):
        validate_payload(RUN_STARTED, {"config_hash": value, "seed": 1})


@pytest.mark.parametrize("kind", [POST_PUBLISHED, POST_ENGAGED])
def test_post_kinds_are_classified_as_social_media(kind: int) -> None:
    assert range_for(kind).domain == "social_media"

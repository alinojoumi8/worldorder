from decimal import Decimal

import pytest

from polis.events.kinds import POST_ENGAGED, POST_PUBLISHED, RUN_STARTED, range_for
from polis.events.schemas import PayloadSchemaError, validate_payload

RUN_STARTED_PAYLOAD = {
    "config_hash": "abc",
    "prompt_manifest": {},
    "model_manifest": {},
    "code_git_sha": "a" * 40,
    "master_seed": 1,
    "completion_cache_manifest_hash": "b" * 64,
    "mechanism_manifest": {},
    "metric_manifest": {},
    "kind_registry_hash": "c" * 64,
    "clock_profile": "test",
    "scale": 1,
}


@pytest.mark.parametrize("field", sorted(RUN_STARTED_PAYLOAD))
def test_required_payload_field_is_enforced(field: str) -> None:
    payload = dict(RUN_STARTED_PAYLOAD)
    payload.pop(field)
    with pytest.raises(PayloadSchemaError, match=field):
        validate_payload(RUN_STARTED, payload)


@pytest.mark.parametrize("value", [Decimal("1"), b"x", {1}, float("nan")])
def test_non_json_payload_is_rejected(value: object) -> None:
    payload = {**RUN_STARTED_PAYLOAD, "config_hash": value}
    with pytest.raises(PayloadSchemaError):
        validate_payload(RUN_STARTED, payload)


@pytest.mark.parametrize("kind", [POST_PUBLISHED, POST_ENGAGED])
def test_post_kinds_are_classified_as_social_media(kind: int) -> None:
    assert range_for(kind).domain == "social_media"

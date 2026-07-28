from pathlib import Path

from polis.agents.actions import ActionType, action_schema_bundle_bytes


def test_checked_in_action_schema_bundle_matches_the_models() -> None:
    path = Path("polis/events/schemas/actions.v1.json")
    payload = action_schema_bundle_bytes()

    assert path.read_bytes() == payload
    for action_type in ActionType:
        assert f'"{action_type.value}":'.encode() in payload

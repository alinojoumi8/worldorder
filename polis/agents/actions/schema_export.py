from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polis.agents.actions.params import PARAMS_MODELS
from polis.agents.actions.types import ActionType
from polis.config.canon import sha256_hex


def action_schema_bundle() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://polis.local/schemas/actions.v1.json",
        "version": 1,
        "actions": {
            action_type.value: PARAMS_MODELS[action_type].model_json_schema()
            for action_type in ActionType
        },
    }


def action_schema_bundle_bytes() -> bytes:
    return (
        json.dumps(
            action_schema_bundle(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def export_action_schema_bundle(path: Path) -> str:
    payload = action_schema_bundle_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return sha256_hex(payload)

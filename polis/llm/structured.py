from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator


def extract_and_validate(
    text: str, schema: Mapping[str, Any]
) -> tuple[Mapping[str, Any] | None, str | None]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = cleaned.find("{")
    if start < 0:
        return None, "no JSON object found"
    depth = 0
    end = -1
    in_string = False
    escaped = False
    for index, character in enumerate(cleaned[start:], start=start):
        if escaped:
            escaped = False
        elif character == "\\" and in_string:
            escaped = True
        elif character == '"':
            in_string = not in_string
        elif not in_string:
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
    if end < 0:
        return None, "unterminated JSON object"
    try:
        value = json.loads(cleaned[start:end])
    except json.JSONDecodeError as exc:
        return None, str(exc)
    if not isinstance(value, Mapping):
        return None, "response must be an object"
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=str)
    return (None, errors[0].message) if errors else (value, None)

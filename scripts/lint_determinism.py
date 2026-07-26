from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_IMPORTS = {"random"}
FORBIDDEN_CALLS = {
    ("datetime", "now"),
    ("time", "time"),
    ("uuid", "uuid4"),
}


def violations(path: Path) -> list[str]:
    result: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                    result.append(f"{path}:{node.lineno}: forbidden import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in FORBIDDEN_IMPORTS:
                result.append(f"{path}:{node.lineno}: forbidden import {node.module}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            pair = (node.func.value.id, node.func.attr)
            if pair in FORBIDDEN_CALLS:
                result.append(f"{path}:{node.lineno}: forbidden call {'.'.join(pair)}")
    return result


def main() -> int:
    roots = [Path(value) for value in sys.argv[1:]] or [Path("polis")]
    found: list[str] = []
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in paths:
            found.extend(violations(path))
    print("\n".join(found))
    return bool(found)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import ast
from pathlib import Path


def test_demography_boundary_uses_only_ports() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative in ("polis/agents/demography.py", "polis/agents/ports.py"):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(name.startswith(("polis.economy", "polis.society")) for name in imported)

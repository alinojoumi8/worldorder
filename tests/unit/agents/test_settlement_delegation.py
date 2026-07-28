from __future__ import annotations

import ast
from pathlib import Path

import pytest

from polis.agents.genesis import mark_dead

ROOT = Path(__file__).resolve().parents[3]
DEMOGRAPHY = ROOT / "polis" / "agents" / "demography.py"


def test_c20_delegates_the_economic_waterfall_exactly_once() -> None:
    tree = ast.parse(DEMOGRAPHY.read_text(encoding="utf-8"))
    estate_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "EstateSettler"
    )
    settle = next(
        node
        for node in estate_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "settle"
    )
    calls = [
        node
        for node in ast.walk(settle)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "settle_death"
    ]

    assert len(calls) == 1


def test_c20_contains_no_economic_waterfall_implementation() -> None:
    tree = ast.parse(DEMOGRAPHY.read_text(encoding="utf-8"))
    forbidden = {
        "cancel_entity",
        "liquidate",
        "write_off_loan",
        "creditor_priority",
        "order_book",
    }
    referenced = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }

    assert forbidden.isdisjoint(referenced)


def test_m1_mark_dead_path_is_disabled_at_m5() -> None:
    with pytest.raises(RuntimeError, match="EstateSettler"):
        mark_dead(None, None, None, None)  # type: ignore[arg-type]

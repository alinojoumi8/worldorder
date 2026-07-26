from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from polis.events.reader import EventReader
from polis.events.types import Event


@dataclass(frozen=True, slots=True)
class CausalNode:
    event: Event
    depth: int
    children: tuple[int, ...]


async def ancestors(
    reader: EventReader,
    run_id: UUID,
    seq: int,
    *,
    max_depth: int = 64,
) -> list[Event]:
    result: list[Event] = []
    seen: set[int] = set()
    current = await reader.get(run_id, seq)
    while current is not None and current.seq not in seen and len(result) <= max_depth:
        seen.add(current.seq)
        result.append(current)
        current = (
            await reader.get(run_id, current.cause_seq) if current.cause_seq is not None else None
        )
    return result


async def descendants(
    reader: EventReader,
    run_id: UUID,
    seq: int,
    *,
    max_depth: int = 8,
    max_nodes: int = 5_000,
) -> list[CausalNode]:
    root = await reader.get(run_id, seq)
    if root is None:
        return []
    queue: list[tuple[Event, int]] = [(root, 0)]
    result: list[CausalNode] = []
    while queue and len(result) < max_nodes:
        event, depth = queue.pop(0)
        children = await reader.by_cause(run_id, event.seq)
        result.append(CausalNode(event, depth, tuple(item.seq for item in children)))
        if depth < max_depth:
            queue.extend((child, depth + 1) for child in children)
            queue.sort(key=lambda item: (item[1], item[0].seq))
    return result


async def explain(
    reader: EventReader,
    run_id: UUID,
    seq: int,
    *,
    max_depth: int = 64,
) -> dict[str, Any]:
    chain = await ancestors(reader, run_id, seq, max_depth=max_depth)
    return {
        "event": chain[0] if chain else None,
        "ancestors": chain[1:],
        "root": chain[-1] if chain else None,
        "depth": max(0, len(chain) - 1),
        "truncated": len(chain) > max_depth,
    }


def has_ancestor_in_range(chain: list[Event], lo: int, hi: int) -> bool:
    return any(lo <= event.kind <= hi for event in chain)

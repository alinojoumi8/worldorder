from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Protocol, TypeVar

import numpy as np

T = TypeVar("T")


class DeterministicRng:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.counter = 0

    def _word(self) -> int:
        value = hashlib.sha256(
            self.seed.to_bytes(8, "big") + self.counter.to_bytes(8, "big")
        ).digest()
        self.counter += 1
        return int.from_bytes(value[:8], "big")

    def random(self) -> float:
        return self._word() / (2**64 - 1)

    def randint(self, start: int, end: int) -> int:
        if end < start:
            raise ValueError("empty integer range")
        return start + self._word() % (end - start + 1)

    def choice(self, values: Sequence[T]) -> T:
        if not values:
            raise ValueError("cannot choose from an empty sequence")
        return values[self._word() % len(values)]

    def shuffle(self, values: list[T]) -> None:
        for index in range(len(values) - 1, 0, -1):
            target = self._word() % (index + 1)
            values[index], values[target] = values[target], values[index]


class SeedSource(Protocol):
    def seed_for(self, namespace: str, entity_id: str = "", tick: int | None = None) -> int: ...


class RngRegistry:
    name: ClassVar[str] = "rng"

    def __init__(self, master_seed: int) -> None:
        self._master_seed = master_seed
        self._draws = 0

    @property
    def master_seed(self) -> int:
        return self._master_seed

    @property
    def draws(self) -> int:
        return self._draws

    def seed_for(self, namespace: str, entity_id: str = "", tick: int | None = None) -> int:
        material = (
            f"{self._master_seed}|{namespace}|{entity_id}|{'' if tick is None else tick}"
        ).encode()
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")

    def get(self, namespace: str, entity_id: str = "", tick: int | None = None) -> DeterministicRng:
        self._draws += 1
        return DeterministicRng(self.seed_for(namespace, entity_id, tick))

    def numpy(
        self, namespace: str, entity_id: str = "", tick: int | None = None
    ) -> np.random.Generator:
        self._draws += 1
        return np.random.default_rng(self.seed_for(namespace, entity_id, tick))

    def dump(self) -> Mapping[str, Any]:
        return {"master_seed": self._master_seed, "version": 1}

    def load(self, state: Mapping[str, Any]) -> None:
        if int(state["master_seed"]) != self._master_seed:
            raise ValueError("checkpoint master seed differs from current run")

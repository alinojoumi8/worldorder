from types import SimpleNamespace

import pytest

from polis.living_city import LivingCityEngine


class RecordingLedger:
    def __init__(self) -> None:
        self.committed_ticks: list[int] = []

    def commit_tick(self, tick: int) -> None:
        self.committed_ticks.append(tick)


class RecordingEconomy:
    def __init__(self) -> None:
        self.ledger = RecordingLedger()
        self.synced_populations: list[object] = []

    def sync_denormalised(self, population: object) -> None:
        self.synced_populations.append(population)


@pytest.mark.asyncio
async def test_economy_finalises_vitals_without_demography_runtime() -> None:
    engine = LivingCityEngine.__new__(LivingCityEngine)
    economy = RecordingEconomy()
    population = object()
    engine.demography = None
    engine.economy = economy  # type: ignore[assignment]
    engine.population = population  # type: ignore[assignment]

    await engine.demography_vitals(SimpleNamespace(tick=7))  # type: ignore[arg-type]

    assert economy.synced_populations == [population]
    assert economy.ledger.committed_ticks == [7]

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from polis.agents.genesis import generate_agents
from polis.config.settings import load_settings
from polis.economy.genesis import create_economy
from polis.economy.invariants import (
    check_money,
    issued_base_money_cents,
    m0_cents,
    m1_cents,
)
from polis.events.kinds import MONEY_ISSUED
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.invariants import Ok
from polis.kernel.rng import RngRegistry
from polis.simulation import run_id_for
from polis.world.generator import generate_world


def build() -> tuple[object, ...]:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={"economy": {"enabled": True}},
    )
    rng = RngRegistry(settings.run.seed)
    world = generate_world(settings.world, rng)
    population = generate_agents(settings.population, world, rng)
    sink = MemoryEventSink()
    log = EventLog(run_id_for(settings), sink)
    result = create_economy(
        settings,
        population,
        world,
        rng,
        run_id_for(settings),
        emit=lambda draft: log.stage(
            draft,
            tick=0,
            sim_time=datetime(2025, 1, 1),
        ),
    )
    return settings, population, log, result


def test_genesis_creates_exact_balanced_m0_distribution() -> None:
    settings, population, log, result = build()
    economy = result.state
    expected_m0 = len(population) * settings.economy.m0_cents_per_capita
    assert len(economy.banks) == 4
    assert len(economy.firms) == 3
    assert issued_base_money_cents(economy.ledger) == expected_m0
    assert m0_cents(economy.ledger) == expected_m0
    assert m1_cents(economy.ledger) == expected_m0 * 9 // 10
    assert economy.ledger.global_balance_cents() == 0
    assert isinstance(check_money(economy.ledger, economy), Ok)
    assert sum(agent.wealth_cents for agent in population) == expected_m0 * 7 // 10
    assert sum(firm.liquid_cents for firm in economy.firms.values()) == expected_m0 * 2 // 10
    assert all(
        firm.capital_cents == settings.firms.capital_ref_cents for firm in economy.firms.values()
    )
    assert sum(bank.capital_cents for bank in economy.banks.values()) == expected_m0 // 10
    assert sum(event.kind == MONEY_ISSUED for event in log.staged()) == 3


def test_genesis_is_byte_stable_for_the_same_seed() -> None:
    _settings_a, _population_a, log_a, result_a = build()
    _settings_b, _population_b, log_b, result_b = build()
    assert result_a.state.dump() == result_b.state.dump()
    assert [event.hash for event in log_a.staged()] == [event.hash for event in log_b.staged()]

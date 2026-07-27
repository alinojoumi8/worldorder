from __future__ import annotations

from datetime import datetime
from pathlib import Path

from polis.agents.cognition.observation import build_observations
from polis.agents.genesis import generate_agents
from polis.config.settings import load_settings
from polis.economy.exchange.models import HoldingState, SecurityState
from polis.economy.genesis import create_economy
from polis.economy_observations import augment_economic_observations
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.rng import RngRegistry
from polis.simulation import run_id_for
from polis.world.generator import generate_world


def test_market_perception_is_resource_bounded_and_read_only() -> None:
    settings = load_settings(Path("configs/m3-smoke.yaml"))
    rng = RngRegistry(settings.run.seed)
    world = generate_world(settings.world, rng)
    population = generate_agents(settings.population, world, rng)
    event_log = EventLog(run_id_for(settings), MemoryEventSink())
    economy = create_economy(
        settings,
        population,
        world,
        rng,
        run_id_for(settings),
        emit=lambda draft: event_log.stage(
            draft,
            tick=0,
            sim_time=datetime(2100, 1, 1),
        ),
    ).state
    firm = min(economy.firms.values(), key=lambda row: row.firm_id)
    economy.exchange.securities["POLS"] = SecurityState(
        symbol="POLS",
        issuer_firm_id=firm.firm_id,
        security_class="common",
        shares_outstanding=100_000,
        listed_tick=1,
        listing_price_cents=1_000,
        last_price_cents=1_000,
        reference_price_cents=1_000,
    )
    economy.exchange.holdings[economy.exchange.holding_key(firm.founder_id, "POLS")] = HoldingState(
        holder_id=firm.founder_id,
        symbol="POLS",
        qty=100_000,
    )
    before_holdings = economy.exchange.dump()["holdings"]
    observations = build_observations(
        population,
        world,
        tick=1,
        sim_time=datetime(2100, 1, 2),
    )

    augmented = augment_economic_observations(observations, population, economy)

    assert economy.exchange.dump()["holdings"] == before_holdings
    founder = augmented[firm.founder_id]
    assert founder.market is not None
    assert "SUBMIT_ORDER" in founder.place.legal_actions
    assert founder.market["liquid_cents"] == economy.ledger.liquid(firm.founder_id)
    assert founder.market["securities"] == (
        {
            "symbol": "POLS",
            "last_price_cents": 1_000,
            "holding_qty": 100_000,
            "available_qty": 100_000,
        },
    )

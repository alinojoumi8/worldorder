from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from polis.agents.state import AgentPopulation
from polis.config.settings import Settings
from polis.economy.ledger import Ledger, Leg, bank_of
from polis.economy.money import allocate, bp
from polis.economy.state import BankState, EconomyState, FirmState
from polis.events.kinds import ACCOUNT_OPENED, BANK_FOUNDED, FIRM_FOUNDED, MONEY_ISSUED
from polis.events.types import Event, NewEvent
from polis.kernel.rng import RngRegistry
from polis.world.api import Place, World

SECTORS = (
    "food",
    "retail",
    "industrial",
    "services",
    "health",
    "education",
    "finance",
    "media",
)


@dataclass(frozen=True, slots=True)
class GenesisResult:
    state: EconomyState
    events: tuple[Event, ...]


def _firm_place(world: World, sector: str, ordinal: int) -> Place:
    preferred = {
        "food": "shop",
        "retail": "shop",
        "industrial": "factory",
        "services": "office",
        "health": "hospital",
        "education": "school",
        "finance": "office",
        "media": "newsroom",
    }[sector]
    places = world.places_of_type(preferred) or world.places_of_type("office") or world.places
    return places[ordinal % len(places)]


def _weighted_ids(
    ids: tuple[str, ...],
    *,
    namespace: str,
    rng: RngRegistry,
) -> tuple[tuple[str, int], ...]:
    return tuple(
        (
            owner_id,
            rng.get(namespace, owner_id, 0).randint(1, 10_000) ** 2,
        )
        for owner_id in ids
    )


def create_economy(
    settings: Settings,
    population: AgentPopulation,
    world: World,
    rng: RngRegistry,
    run_id: UUID,
    *,
    emit: Callable[[NewEvent], Event],
) -> GenesisResult:
    ledger = Ledger(run_id)
    events: list[Event] = []
    banks: dict[str, BankState] = {}
    bank_places = world.places_of_type("bank") or world.places_of_type("office") or world.places
    commercial_bank_ids = tuple(
        f"bk_{ordinal + 1:02d}" for ordinal in range(settings.economy.initial_banks)
    )

    issuance = ledger.open_account("iss", "bk_cb", "central_bank", tick=0)
    central_reserves = ledger.open_account("res", "bk_cb", "central_bank", tick=0)
    central_liability = ledger.open_account("dpl", "bk_cb", "central_bank", tick=0)
    treasury = ledger.open_account(
        "dep",
        "gv_treasury",
        "government",
        bank_id="bk_cb",
        tick=0,
    )
    del treasury
    for ordinal, bank_id in enumerate(commercial_bank_ids):
        place = bank_places[ordinal % len(bank_places)]
        reserves = ledger.open_account("res", bank_id, "bank", tick=0)
        liability = ledger.open_account("dpl", bank_id, "bank", tick=0)
        banks[bank_id] = BankState(
            bank_id,
            f"Polis Bank {ordinal + 1}",
            place.place_id,
            reserves,
            liability,
            False,
            reserve_ratio_bp=settings.banking.reserve_ratio_bp,
        )
        events.append(
            emit(
                NewEvent(
                    BANK_FOUNDED,
                    {
                        "bank_id": bank_id,
                        "name": banks[bank_id].name,
                        "place_id": place.place_id,
                        "founder_id": None,
                        "capital_cents": 0,
                        "reserve_ratio_bp": settings.banking.reserve_ratio_bp,
                        "is_central": False,
                    },
                )
            )
        )

    central_place = bank_places[0]
    banks["bk_cb"] = BankState(
        "bk_cb",
        "Central Bank of Polis",
        central_place.place_id,
        central_reserves,
        central_liability,
        True,
        reserve_ratio_bp=0,
    )
    events.append(
        emit(
            NewEvent(
                BANK_FOUNDED,
                {
                    "bank_id": "bk_cb",
                    "name": banks["bk_cb"].name,
                    "place_id": central_place.place_id,
                    "founder_id": None,
                    "capital_cents": 0,
                    "reserve_ratio_bp": 0,
                    "is_central": True,
                },
            )
        )
    )

    firm_count = settings.economy.initial_firms or max(1, len(population) * 6 // 100)
    adult_ids = tuple(
        agent.agent_id for agent in population if 18 <= agent.age_years < 65
    ) or tuple(agent.agent_id for agent in population)
    firms: dict[str, FirmState] = {}
    for ordinal in range(firm_count):
        firm_id = f"fm_{ordinal + 1:04d}"
        sector = SECTORS[ordinal % len(SECTORS)]
        place = _firm_place(world, sector, ordinal)
        founder_id = adult_ids[ordinal % len(adult_ids)]
        bank_id = commercial_bank_ids[ordinal % len(commercial_bank_ids)]
        deposit = ledger.open_account(
            "dep",
            firm_id,
            "firm",
            bank_id=bank_id,
            tick=0,
        )
        productivity = rng.get("firms.seed", firm_id, 0).randint(8_000, 12_000)
        firms[firm_id] = FirmState(
            firm_id,
            f"{sector.title()} Cooperative {ordinal + 1}",
            sector,
            place.place_id,
            founder_id,
            deposit,
            productivity,
        )
        events.append(
            emit(
                NewEvent(
                    FIRM_FOUNDED,
                    {
                        "firm_id": firm_id,
                        "founder_id": founder_id,
                        "name": firms[firm_id].name,
                        "sector": sector,
                        "place_id": place.place_id,
                        "initial_capital_cents": 0,
                        "ledger_account_id": deposit,
                        "is_startup": False,
                        "registration_fee_cents": 0,
                    },
                    actor_id=founder_id,
                    subject_ids=(firm_id,),
                )
            )
        )

    agent_deposits: dict[str, str] = {}
    for ordinal, agent in enumerate(population):
        bank_id = commercial_bank_ids[ordinal % len(commercial_bank_ids)]
        deposit = ledger.open_account(
            "dep",
            agent.agent_id,
            "agent",
            bank_id=bank_id,
            tick=0,
        )
        agent_deposits[agent.agent_id] = deposit

    for account in ledger.accounts():
        events.append(
            emit(
                NewEvent(
                    ACCOUNT_OPENED,
                    {
                        "account_id": account.account_id,
                        "owner_id": account.owner_id,
                        "owner_type": account.owner_type,
                        "bank_id": account.bank_id,
                        "account_type": account.code,
                        "code": account.code,
                    },
                    subject_ids=(account.owner_id,),
                )
            )
        )

    total_m0 = len(population) * settings.economy.m0_cents_per_capita
    household_pool = bp(total_m0, settings.economy.household_share_bp)
    firm_pool = bp(total_m0, settings.economy.firm_share_bp)
    bank_pool = total_m0 - household_pool - firm_pool
    household_allocations = allocate(
        household_pool,
        _weighted_ids(
            tuple(sorted(agent_deposits)),
            namespace="economy.genesis.households",
            rng=rng,
        ),
    )
    firm_allocations = allocate(
        firm_pool,
        _weighted_ids(
            tuple(sorted(firms)),
            namespace="economy.genesis.firms",
            rng=rng,
        ),
    )
    bank_allocations = allocate(
        bank_pool,
        tuple((bank_id, 1) for bank_id in commercial_bank_ids),
    )

    classes = (
        (
            "households",
            household_allocations,
            agent_deposits,
        ),
        (
            "firms",
            firm_allocations,
            {firm_id: firms[firm_id].ledger_account_id for firm_id in sorted(firms)},
        ),
    )
    for class_name, allocations, deposits in classes:
        amount = sum(allocations.values())
        legs: list[Leg] = []
        by_bank: dict[str, int] = {}
        for owner_id, cents in sorted(allocations.items()):
            if cents == 0:
                continue
            deposit = deposits[owner_id]
            deposit_bank_id = bank_of(deposit)
            if deposit_bank_id is None:
                raise RuntimeError("commercial deposit is missing its bank")
            legs.append(Leg(deposit, 1, cents, "issuance"))
            by_bank[deposit_bank_id] = by_bank.get(deposit_bank_id, 0) + cents
        for bank_id, cents in sorted(by_bank.items()):
            legs.extend(
                (
                    Leg(
                        banks[bank_id].deposit_liability_account_id,
                        -1,
                        cents,
                        "issuance",
                    ),
                    Leg(banks[bank_id].reserve_account_id, 1, cents, "issuance"),
                )
            )
        legs.append(Leg(issuance, -1, amount, "issuance"))
        expected_txn_id = ledger.next_txn_id(0)
        issued_event = emit(
            NewEvent(
                MONEY_ISSUED,
                {
                    "amount_cents": amount,
                    "recipient_account_id": f"class:{class_name}",
                    "instrument": "reserves",
                    "purpose": "genesis",
                    "txn_id": str(expected_txn_id),
                },
            )
        )
        txn_id = ledger.issue_base_money(legs, tick=0, cause=issued_event)
        if txn_id != expected_txn_id:
            raise RuntimeError("ledger transaction ordinal diverged during genesis")
        events.append(issued_event)

    bank_legs = [
        Leg(banks[bank_id].reserve_account_id, 1, cents, "issuance")
        for bank_id, cents in sorted(bank_allocations.items())
        if cents
    ]
    bank_legs.append(Leg(issuance, -1, bank_pool, "issuance"))
    expected_txn_id = ledger.next_txn_id(0)
    issued_event = emit(
        NewEvent(
            MONEY_ISSUED,
            {
                "amount_cents": bank_pool,
                "recipient_account_id": "class:banks",
                "instrument": "reserves",
                "purpose": "genesis",
                "txn_id": str(expected_txn_id),
            },
        )
    )
    txn_id = ledger.issue_base_money(bank_legs, tick=0, cause=issued_event)
    if txn_id != expected_txn_id:
        raise RuntimeError("ledger transaction ordinal diverged during genesis")
    events.append(issued_event)

    eligible_agents = [
        agent for agent in population if 18 <= agent.age_years < settings.labour.retirement_age
    ]
    target_base, target_extra = divmod(len(eligible_agents), max(1, len(firms)))
    for ordinal, firm in enumerate(firms.values()):
        firm.target_headcount = max(1, target_base + (1 if ordinal < target_extra else 0))
        firm.capital_cents = settings.firms.capital_ref_cents
    for agent in eligible_agents:
        if agent.employment_status == "employed":
            agent.employment_status = "unemployed"

    state = EconomyState(ledger, banks, firms)
    state.sync_denormalised(population)
    return GenesisResult(state, tuple(events))

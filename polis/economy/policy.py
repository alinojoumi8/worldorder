from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from polis.agents.actions.types import Action, ActionType, make_action
from polis.agents.state import AgentPopulation
from polis.config.mechanisms import mechanism
from polis.config.settings import Settings
from polis.economy.banking import BankingEngine
from polis.economy.exchange.engine import ExchangeEngine
from polis.economy.firms import FirmEngine
from polis.economy.goods import GoodsEngine
from polis.economy.labour import (
    LabourMarket,
    Occupation,
    active_employment,
    match_score_bp,
    visibility_slice,
)
from polis.economy.state import EconomyState, FirmState
from polis.economy.ventures import VentureEngine
from polis.events.types import Event, NewEvent
from polis.kernel.rng import RngRegistry
from polis.llm.router import LLMRouter
from polis.world.api import World

Emit = Callable[[NewEvent], Event]


@mechanism(
    "labour.vacancy_autopost",
    entails=(
        "An active firm below its target headcount posts a vacancy after a bounded "
        "fallback delay; this guarantees vacancy creation but not a match."
    ),
    config_key="mechanisms.labour_vacancy_autopost",
)
def should_autopost(
    firm: FirmState,
    economy: EconomyState,
    *,
    max_open_vacancies: int,
) -> bool:
    open_vacancies = [
        row
        for row in economy.vacancies.values()
        if row.firm_id == firm.firm_id and row.status == "open" and row.headcount > 0
    ]
    committed_headcount = firm.headcount + sum(row.headcount for row in open_vacancies)
    return (
        firm.status == "active"
        and committed_headcount < firm.target_headcount
        and len(open_vacancies) < max_open_vacancies
    )


class MechanicalPolicy:
    """Explicit classical-ABM decisions used by the reflex-only research baseline."""

    def __init__(
        self,
        settings: Settings,
        population: AgentPopulation,
        world: World,
        economy: EconomyState,
        rng: RngRegistry,
        occupations: Mapping[str, Occupation],
        router: LLMRouter,
    ) -> None:
        self.settings = settings
        self.population = population
        self.world = world
        self.economy = economy
        self.rng = rng
        self.occupations = occupations
        self.labour = LabourMarket(settings, population, world, economy, rng, occupations)
        self.firms = FirmEngine(settings, economy, rng)
        self.goods = GoodsEngine(settings, population, world, economy, rng)
        self.exchange = ExchangeEngine(settings, population, economy, rng)
        self.banking = BankingEngine(settings, population, economy, rng, router)
        self.ventures = VentureEngine(
            settings,
            population,
            economy,
            rng,
            router,
            self.exchange,
            self.banking.credit_context,
        )

    async def step(
        self,
        tick: int,
        emit: Emit,
        actions: Sequence[Action] = (),
    ) -> tuple[Event, ...]:
        if self.settings.ventures.acceptance_fixture:
            actions = (*actions, *self._m3_acceptance_actions(tick))
        events: list[Event] = list(self.labour.expire(tick, emit))
        first_actions = tuple(
            action for action in self._first_actions(tick) if not self._stay_blocks(action)
        )
        events.extend(self.labour.resolve(first_actions, tick, emit))
        shortlisted = self.labour.screen_pending(tick, emit)
        offer_actions = tuple(
            action
            for action in self._offer_actions(shortlisted, tick)
            if not self._stay_blocks(action)
        )
        events.extend(self.labour.resolve(offer_actions, tick, emit))
        events.extend(self.goods.expire_durables(tick, emit))
        goods_actions = tuple(
            action
            for action in self.goods.mechanical_actions(tick)
            if not self._stay_blocks(action)
        )
        events.extend(self.goods.resolve(goods_actions, tick, emit))
        events.extend(self.labour.run_payroll(tick, emit))
        events.extend(self.labour.decay_unused_skills(tick, emit))
        events.extend(self.firms.run_daily(tick, emit))
        events.extend(self.goods.compute_cpi(tick, emit))
        events.extend(self.exchange.resolve(actions, tick, emit))
        events.extend(await self.banking.step(tick, emit))
        events.extend(await self.ventures.resolve(actions, tick, emit))
        events.append(self.labour.emit_summary(tick, emit))
        self.economy.sync_denormalised(self.population)
        self.economy.ledger.commit_tick(tick)
        return tuple(events)

    def _stay_blocks(self, action: Action) -> bool:
        if action.type in {
            ActionType.FILE_BANKRUPTCY,
            ActionType.WORK,
            ActionType.MOVE_TO,
            ActionType.NULL_ACTION,
            ActionType.SLEEP,
            ActionType.EAT,
        }:
            return False
        candidates = {action.actor_id}
        for key in ("entity_id", "firm_id", "acquirer_id", "target_id"):
            value = action.params.get(key)
            if value:
                candidates.add(str(value))
        return any(
            case.status == "open" and case.entity_id in candidates
            for case in self.economy.ventures.bankruptcies.values()
        )

    def _m3_acceptance_actions(self, tick: int) -> tuple[Action, ...]:
        """Deterministic integration fixture; never enabled in research configs."""
        agents = [agent.agent_id for agent in self.population if agent.alive][:4]
        if len(agents) < 4:
            return ()
        founder_a, founder_b, investor_a, investor_b = agents
        startup_a = next(
            (row for row in self.economy.ventures.startups.values() if row.founder_id == founder_a),
            None,
        )
        startup_b = next(
            (row for row in self.economy.ventures.startups.values() if row.founder_id == founder_b),
            None,
        )
        actions: list[Action] = []

        def scripted(
            actor_id: str,
            action_type: ActionType,
            params: dict[str, object],
            ordinal: int,
        ) -> None:
            actions.append(
                make_action(
                    actor_id=actor_id,
                    tick=tick,
                    action_type=action_type,
                    params=params,
                    origin="scripted",
                    reasoning="M3 acceptance fixture; not a research-policy decision",
                    ordinal=10_000 + ordinal,
                )
            )

        if tick == 1:
            for ordinal, (actor_id, name) in enumerate(
                ((founder_a, "Fixture Capital"), (founder_b, "Fixture Target"))
            ):
                scripted(
                    actor_id,
                    ActionType.FOUND_COMPANY,
                    {
                        "name": name,
                        "sector": "services",
                        "place_id": self.population[actor_id].home_place_id,
                        "initial_capital_cents": 30_000,
                        "is_startup": True,
                        "is_fund": False,
                        "thesis": f"{name} deterministic acceptance path",
                    },
                    ordinal,
                )
        elif tick == 2 and startup_a is not None and startup_b is not None:
            underwriter = next(
                bank.bank_id
                for bank in sorted(self.economy.banks.values(), key=lambda row: row.bank_id)
                if not bank.is_central
            )
            scripted(
                investor_a,
                ActionType.ISSUE_TERM_SHEET,
                {
                    "startup_id": startup_b.startup_id,
                    "investor_id": investor_a,
                    "pre_money_cents": 100_000,
                    "amount_cents": 5_000,
                    "security": "preferred",
                    "liq_pref_bp": 10_000,
                    "participating": False,
                    "pro_rata": True,
                    "board_seat": False,
                    "option_pool_bp": 1_000,
                    "anti_dilution": "broad_weighted",
                },
                0,
            )
            scripted(
                founder_a,
                ActionType.IPO_LIST,
                {
                    "firm_id": startup_a.firm_id,
                    "symbol": "FIX",
                    "shares_offered": 100,
                    "primary_shares": 100,
                    "secondary_shares": 0,
                    "price_low_cents": 100,
                    "price_high_cents": 120,
                    "underwriter_bank_id": underwriter,
                },
                1,
            )
        elif tick == 3 and startup_b is not None:
            term = next(
                (
                    row
                    for row in self.economy.ventures.term_sheets.values()
                    if row.startup_id == startup_b.startup_id and row.status == "open"
                ),
                None,
            )
            if term is not None:
                scripted(
                    investor_a,
                    ActionType.INVEST,
                    {
                        "target_id": startup_b.startup_id,
                        "cents": 5_000,
                        "instrument": "round",
                        "term_sheet_id": term.term_sheet_id,
                    },
                    0,
                )
            for ordinal, investor in enumerate((investor_a, investor_b), start=1):
                scripted(
                    investor,
                    ActionType.SUBMIT_ORDER,
                    {
                        "symbol": "FIX",
                        "side": "buy",
                        "order_type": "limit",
                        "qty": 50,
                        "limit_price_cents": 110,
                        "flags": ("ipo",),
                    },
                    ordinal,
                )
        elif tick == 4 and startup_a is not None and startup_b is not None:
            scripted(
                founder_a,
                ActionType.ACQUIRE,
                {
                    "acquirer_id": startup_a.firm_id,
                    "target_id": startup_b.firm_id,
                    "offer_cents": 20_000,
                    "consideration": "cash",
                    "stock_ratio_bp": 0,
                    "integration_mode": "absorb",
                    "financing": "cash",
                },
                0,
            )
        elif tick == 5 and startup_b is not None:
            deal = next(
                (
                    row
                    for row in self.economy.ventures.acquisitions.values()
                    if row.target_id == startup_b.firm_id and row.status == "proposed"
                ),
                None,
            )
            if deal is not None:
                scripted(
                    founder_b,
                    ActionType.SELL_STAKE,
                    {
                        "firm_id": startup_b.firm_id,
                        "qty": self.settings.ventures.founder_shares,
                        "deal_id": deal.deal_id,
                    },
                    0,
                )
            scripted(
                investor_a,
                ActionType.SUBMIT_ORDER,
                {
                    "symbol": "FIX",
                    "side": "sell",
                    "order_type": "limit",
                    "qty": 10,
                    "limit_price_cents": 105,
                },
                1,
            )
            scripted(
                investor_b,
                ActionType.SUBMIT_ORDER,
                {
                    "symbol": "FIX",
                    "side": "buy",
                    "order_type": "limit",
                    "qty": 10,
                    "limit_price_cents": 105,
                },
                2,
            )
        elif tick == 6 and startup_a is not None:
            scripted(
                founder_a,
                ActionType.DECLARE_DIVIDEND,
                {"firm_id": startup_a.firm_id, "total_cents": 100},
                0,
            )
        elif tick == 7 and startup_a is not None:
            scripted(
                founder_a,
                ActionType.FILE_BANKRUPTCY,
                {"entity_id": startup_a.firm_id, "reason": "voluntary"},
                0,
            )
        return tuple(actions)

    def _first_actions(self, tick: int) -> tuple[Action, ...]:
        actions: list[Action] = []
        ordinals: dict[str, int] = {}

        for employment in sorted(
            self.economy.employments.values(),
            key=lambda row: row.employment_id,
        ):
            if (
                employment.started_tick <= tick
                and employment.ended_tick is None
                and self.population[employment.agent_id].alive
            ):
                actions.append(
                    self._action(
                        employment.agent_id,
                        tick,
                        ActionType.WORK,
                        {
                            "employment_id": employment.employment_id,
                            "effort_bp": 10_000,
                        },
                        ordinals,
                    )
                )

        offers_by_agent: dict[str, list[object]] = {}
        for offer in self.economy.offers.values():
            if offer.status == "open" and offer.made_tick < tick and offer.expires_tick >= tick:
                offers_by_agent.setdefault(offer.agent_id, []).append(offer)
        for agent_id, raw_offers in sorted(offers_by_agent.items()):
            if active_employment(self.economy, agent_id, tick) is not None:
                continue
            offers = sorted(
                raw_offers,
                key=lambda row: (-row.wage_cents, row.offer_id),  # type: ignore[attr-defined]
            )
            chosen = offers[0]
            actions.append(
                self._action(
                    agent_id,
                    tick,
                    ActionType.ACCEPT_OFFER,
                    {"offer_id": chosen.offer_id},  # type: ignore[attr-defined]
                    ordinals,
                )
            )

        if self.settings.mechanisms.get("labour_vacancy_autopost", "on") != "off":
            for firm in sorted(self.economy.firms.values(), key=lambda row: row.firm_id):
                if not should_autopost(
                    firm,
                    self.economy,
                    max_open_vacancies=self.settings.labour.max_open_vacancies_per_firm,
                ):
                    continue
                occupation = self._occupation_for(firm)
                committed = firm.headcount + sum(
                    row.headcount
                    for row in self.economy.vacancies.values()
                    if row.firm_id == firm.firm_id and row.status == "open"
                )
                headcount = max(1, firm.target_headcount - committed)
                actions.append(
                    self._action(
                        firm.founder_id,
                        tick,
                        ActionType.POST_VACANCY,
                        {
                            "firm_id": firm.firm_id,
                            "occupation": occupation.id,
                            "wage_offer_cents": max(
                                self.settings.labour.minimum_wage_cents,
                                self.settings.economy.median_wage_cents // 24,
                            ),
                            "headcount": headcount,
                        },
                        ordinals,
                    )
                )

        for agent in self.population:
            if (
                not agent.alive
                or not 18 <= agent.age_years < self.settings.labour.retirement_age
                or active_employment(self.economy, agent.agent_id, tick) is not None
                or agent.employment_status in {"child", "student", "retired", "dead"}
            ):
                continue
            visible = visibility_slice(
                agent,
                self.economy,
                self.world,
                self.occupations,
                self.rng,
                tick=tick,
                limit=self.settings.labour.vacancy_visibility_k,
            )
            candidates = [
                vacancy
                for vacancy in visible
                if not any(
                    application.agent_id == agent.agent_id
                    and application.vacancy_id == vacancy.vacancy_id
                    for application in self.economy.applications.values()
                )
            ]
            if not candidates:
                continue
            vacancy = max(
                candidates,
                key=lambda row: (
                    match_score_bp(
                        agent,
                        row,
                        self.occupations[row.occupation],
                        ticks_per_sim_day=self.settings.clock.ticks_per_sim_day,
                        ticks_per_sim_year=self.settings.clock.days_per_sim_year
                        * self.settings.clock.ticks_per_sim_day,
                    ),
                    -row.posted_tick,
                    row.vacancy_id,
                ),
            )
            actions.append(
                self._action(
                    agent.agent_id,
                    tick,
                    ActionType.APPLY_FOR_JOB,
                    {
                        "vacancy_id": vacancy.vacancy_id,
                        "asked_wage_cents": vacancy.wage_offer_cents,
                    },
                    ordinals,
                )
            )
        return tuple(actions)

    def _offer_actions(
        self,
        shortlisted: Sequence[object],
        tick: int,
    ) -> tuple[Action, ...]:
        actions: list[Action] = []
        ordinals: dict[str, int] = {}
        available_by_vacancy: dict[str, int] = {
            vacancy.vacancy_id: vacancy.headcount
            for vacancy in self.economy.vacancies.values()
            if vacancy.status == "open"
        }
        ranked = sorted(
            shortlisted,
            key=lambda row: (
                row.vacancy_id,  # type: ignore[attr-defined]
                -(row.match_score_bp or 0),  # type: ignore[attr-defined]
                row.rank or 0,  # type: ignore[attr-defined]
            ),
        )
        for application in ranked:
            vacancy_id = application.vacancy_id  # type: ignore[attr-defined]
            if available_by_vacancy.get(vacancy_id, 0) <= 0:
                continue
            vacancy = self.economy.vacancies[vacancy_id]
            firm = self.economy.firms[vacancy.firm_id]
            actions.append(
                self._action(
                    firm.founder_id,
                    tick,
                    ActionType.MAKE_OFFER,
                    {
                        "application_id": application.application_id,  # type: ignore[attr-defined]
                        "wage_cents": vacancy.wage_offer_cents,
                    },
                    ordinals,
                )
            )
            available_by_vacancy[vacancy_id] -= 1
        return tuple(actions)

    def _occupation_for(self, firm: FirmState) -> Occupation:
        choices = [
            occupation
            for occupation in self.occupations.values()
            if firm.sector in occupation.sectors
        ]
        if not choices:
            choices = list(self.occupations.values())
        return sorted(choices, key=lambda row: row.id)[0]

    def _action(
        self,
        actor_id: str,
        tick: int,
        action_type: ActionType,
        params: dict[str, object],
        ordinals: dict[str, int],
    ) -> Action:
        ordinal = ordinals.get(actor_id, 0)
        ordinals[actor_id] = ordinal + 1
        return make_action(
            actor_id=actor_id,
            tick=tick,
            action_type=action_type,
            params=params,
            origin="scripted",
            reasoning="MechanicalPolicy baseline decision",
            ordinal=ordinal,
        )

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from polis.agents.actions import (
    ActionType,
    GateFailure,
    InstitutionSlot,
    ValidationContext,
    make_action,
)
from polis.config.runtime import RuntimeConfig
from polis.config.settings import PolitySettings, load_settings
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.policy import FiscalProjector, MemoryPolicyRepository, PolicyEngine
from polis.society.polity import (
    POLITY_ACTIONS,
    ElectionOffice,
    ExposureLedger,
    OfficeRegister,
    PartyRegistry,
    PolityResolver,
    VoteModel,
)


class Beliefs:
    def value(self, agent_id: str, proposition: str) -> float:
        del agent_id, proposition
        return 0.0

    def confidence(self, agent_id: str, proposition: str) -> float:
        del agent_id, proposition
        return 1.0


class Graph:
    def neighbours(self, agent_id: str, *, min_strength: float = 0.0):
        del agent_id, min_strength
        return ()

    def strength(self, a_id: str, b_id: str, tie_type: str) -> float:
        del a_id, b_id, tie_type
        return 0.0

    def trust(self, a_id: str, b_id: str, tie_type: str) -> float:
        del a_id, b_id, tie_type
        return 0.0


class Ledger:
    def can_pay(self, payer_id: str, cents: int) -> bool:
        del payer_id
        return cents >= 0

    def next_transfer_id(self, tick: int) -> str:
        return f"txn_{tick}"

    def post_transfer(self, *args, **kwargs) -> str:
        del args
        return self.next_transfer_id(int(kwargs["tick"]))


class Outlets:
    def get(self, outlet_id: str):
        del outlet_id
        return None

    def live(self):
        return ()


class World:
    def __init__(self) -> None:
        self.locations: dict[str, object] = {}

    def occupancy(self, place_id: str):
        del place_id
        return ()

    def place(self, place_id: str):
        del place_id
        return SimpleNamespace(owner_id=None)


def _resolver() -> tuple[PolityResolver, RuntimeConfig]:
    settings = load_settings(Path("configs/smoke.yaml"))
    runtime = RuntimeConfig(settings)
    cfg = PolitySettings(
        party_founding_fee_cents=0,
        candidacy_deposit_cents=0,
        initiative_signatures=2,
    )
    clock = Clock(PROFILES["microscope"])
    log = EventLog(UUID(int=1824), MemoryEventSink())
    rng = RngRegistry(1824)
    beliefs = Beliefs()
    graph = Graph()
    ledger = Ledger()
    parties = PartyRegistry(
        log=log,
        clock=clock,
        beliefs=beliefs,
        cfg=cfg,
        ledger=ledger,
    )
    offices = OfficeRegister(log=log, clock=clock, cfg=cfg)
    exposure = ExposureLedger(half_life_ticks=10)
    vote_model = VoteModel(
        rng=rng,
        beliefs=beliefs,
        graph=graph,
        parties=parties,
        offices=offices,
        exposure=exposure,
        cfg=cfg,
        clock=clock,
    )
    elections = ElectionOffice(
        log=log,
        clock=clock,
        rng=rng,
        cfg=cfg,
        parties=parties,
        offices=offices,
        vote_model=vote_model,
        exposure=exposure,
        runtime=runtime,
        ledger=ledger,
    )
    policy = PolicyEngine(
        runtime=runtime,
        log=log,
        clock=clock,
        offices=offices,
        fiscal=FiscalProjector(),
        repo=MemoryPolicyRepository(),
        cfg=cfg,
    )
    return (
        PolityResolver(
            log=log,
            clock=clock,
            rng=rng,
            parties=parties,
            elections=elections,
            offices=offices,
            policy=policy,
            exposure=exposure,
            graph=graph,
            beliefs=beliefs,
            outlets=Outlets(),
            world=World(),
            ledger=ledger,
            runtime=runtime,
            cfg=cfg,
        ),
        runtime,
    )


def _ctx(tick: int) -> ValidationContext:
    return ValidationContext(
        observation=SimpleNamespace(place=SimpleNamespace(place_id="pl_town_hall")),
        state=SimpleNamespace(
            age_years=30,
            alive=True,
            incarcerated=False,
            criminal_record=(),
        ),
        tick=tick,
    )


def test_polity_resolver_owns_exact_slot_and_action_set() -> None:
    assert PolityResolver.slot == InstitutionSlot.POLITY
    assert PolityResolver.handles == POLITY_ACTIONS
    assert (
        frozenset(
            {
                ActionType.FOUND_PARTY,
                ActionType.JOIN_PARTY,
                ActionType.ANNOUNCE_CANDIDACY,
                ActionType.CAMPAIGN,
                ActionType.VOTE,
                ActionType.PROPOSE_POLICY,
                ActionType.LOBBY,
            }
        )
        == POLITY_ACTIONS
    )


def test_feed_proposal_fails_capability_when_feed_regulation_is_disabled() -> None:
    resolver, _runtime = _resolver()
    action = make_action(
        actor_id="ag_citizen",
        tick=1,
        action_type=ActionType.PROPOSE_POLICY,
        params={
            "parameter": "society.feed_algorithm",
            "proposed_value": "chronological",
            "cosigners": ["ag_a", "ag_b"],
        },
    )

    failure = resolver.check_capability(action, _ctx(1))

    assert isinstance(failure, GateFailure)
    assert failure.reason == "capability"
    assert "outside the policy registry" in failure.detail


def test_campaign_cap_is_read_from_live_runtime_overlay() -> None:
    resolver, runtime = _resolver()
    action = make_action(
        actor_id="ag_candidate",
        tick=2,
        action_type=ActionType.CAMPAIGN,
        params={
            "candidacy_id": "ca_one",
            "amount_cents": 101,
            "channel": "canvass",
        },
    )

    assert resolver.check_resources(action, _ctx(1)) is None
    runtime.enact(
        "polity.campaign_cap_cents",
        100,
        2,
        "py_cap",
        1,
        enacted_tick=1,
    )
    failure = resolver.check_resources(action, _ctx(2))

    assert isinstance(failure, GateFailure)
    assert failure.reason == "resources"
    assert "live policy cap" in failure.detail

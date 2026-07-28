import inspect
from types import SimpleNamespace
from uuid import UUID

from polis.agents.actions.params.polity import FoundPartyParams
from polis.config.settings import PolitySettings
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.polity import (
    Candidacy,
    ExposureLedger,
    OfficeRegister,
    PartyRegistry,
    VoteModel,
)


class Beliefs:
    def value(self, agent_id: str, proposition: str) -> float:
        del agent_id, proposition
        return 0.8

    def confidence(self, agent_id: str, proposition: str) -> float:
        del agent_id, proposition
        return 1.0


class Graph:
    tie = SimpleNamespace(
        a_id="ag_voter",
        b_id="ag_friend",
        strength=1.0,
        valence=1.0,
    )

    def neighbours(self, agent_id: str, *, min_strength: float = 0.0):
        del min_strength
        return (self.tie,) if agent_id == "ag_voter" else ()

    def strength(self, a_id: str, b_id: str, tie_type: str) -> float:
        del a_id, b_id, tie_type
        return 1.0

    def trust(self, a_id: str, b_id: str, tie_type: str) -> float:
        del a_id, b_id, tie_type
        return 1.0


class Platform:
    def posts_in_window(self, tick: int, window_ticks: int):
        assert window_ticks == 30 * PROFILES["microscope"].ticks_per_sim_day
        return (
            SimpleNamespace(
                author_id="ag_friend",
                stance_proposition="tax.vat.should_rise",
                stance_value=0.8,
                tick=tick,
            ),
        )


def _model() -> tuple[VoteModel, Candidacy]:
    clock = Clock(PROFILES["microscope"])
    log = EventLog(UUID(int=1823), MemoryEventSink())
    cfg = PolitySettings(party_founding_fee_cents=0)
    beliefs = Beliefs()
    parties = PartyRegistry(log=log, clock=clock, beliefs=beliefs, cfg=cfg)
    party, _events = parties.found(
        "ag_candidate",
        FoundPartyParams(
            name="Civic",
            platform={"tax.vat.should_rise": 0.8},
            founding_member_ids=("ag_candidate", "ag_voter", "ag_friend"),
        ),
        1,
    )
    parties.join("ag_voter", party.party_id, 1)
    parties.join("ag_friend", party.party_id, 1)
    offices = OfficeRegister(log=log, clock=clock, cfg=cfg)
    offices.assume("council", "ag_candidate", 1, via="el_prior", salary_cents=0)
    exposure = ExposureLedger(half_life_ticks=10)
    exposure.record("ca_one", ("ag_voter",), "ads", 2)
    model = VoteModel(
        rng=RngRegistry(1823),
        beliefs=beliefs,
        graph=Graph(),
        parties=parties,
        offices=offices,
        exposure=exposure,
        cfg=cfg,
        clock=clock,
        platform=Platform(),
        income_statement=lambda _agent, _tick: {
            "annual_income_cents": 1_000_000,
            "hourly_wage_cents": 1_000,
            "annual_hours": 2_000,
        },
    )
    return (
        model,
        Candidacy(
            "ca_one",
            "el_one",
            "ag_candidate",
            party.party_id,
            {"tax.vat.should_rise": 0.8},
            0,
            0,
        ),
    )


def test_vote_features_cover_six_terms_and_social_reads_posts() -> None:
    model, candidacy = _model()

    features = model.features("ag_voter", candidacy, "el_one", 2)

    assert tuple(features) == VoteModel.FEATURES
    assert features == {
        "congruence": 1.0,
        "self_interest": 0.0,
        "social": 1.0,
        "media": 1.0,
        "party_id": 1.0,
        "incumbency": 1.0,
    }
    assert "votes" not in inspect.getsource(VoteModel._social)
    assert (
        model.self_interest(
            "ag_voter",
            {"labour.minimum_wage_cents": 2_000},
            2,
        )
        > 0
    )


def test_reflex_ballot_records_all_components_plus_namespaced_gumbel() -> None:
    first_model, candidacy = _model()
    second_model, second_candidacy = _model()

    first = first_model.choose(
        "ag_voter",
        (candidacy,),
        {key: 1.0 for key in VoteModel.FEATURES},
        "el_one",
        2,
    )
    second = second_model.choose(
        "ag_voter",
        (second_candidacy,),
        {key: 1.0 for key in VoteModel.FEATURES},
        "el_one",
        2,
    )

    assert first == second
    assert set(first.utility) == {*VoteModel.FEATURES, "epsilon"}
    assert first.origin == "reflex"

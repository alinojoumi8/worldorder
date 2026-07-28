from pathlib import Path
from uuid import UUID

import pytest

from polis.config.runtime import RuntimeConfig
from polis.config.settings import PolitySettings, load_settings
from polis.events.kinds import CANDIDACY_DEPOSITS_REFUNDED
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.kernel.rng import RngRegistry
from polis.society.polity import (
    Ballot,
    Candidacy,
    Election,
    ElectionOffice,
    ExposureLedger,
    NullPolityLedger,
    OfficeRegister,
    PartyRegistry,
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


def _office(*, seats: int = 1, ledger: NullPolityLedger | None = None) -> ElectionOffice:
    clock = Clock(PROFILES["microscope"])
    cfg = PolitySettings(party_founding_fee_cents=0, candidacy_deposit_cents=0)
    log = EventLog(UUID(int=1821), MemoryEventSink())
    beliefs = Beliefs()
    parties = PartyRegistry(log=log, clock=clock, beliefs=beliefs, cfg=cfg)
    offices = OfficeRegister(log=log, clock=clock, cfg=cfg)
    exposure = ExposureLedger(half_life_ticks=10)
    vote_model = VoteModel(
        rng=RngRegistry(1821),
        beliefs=beliefs,
        graph=Graph(),
        parties=parties,
        offices=offices,
        exposure=exposure,
        cfg=cfg,
        clock=clock,
    )
    elections = ElectionOffice(
        log=log,
        clock=clock,
        rng=RngRegistry(1821),
        cfg=cfg,
        parties=parties,
        offices=offices,
        vote_model=vote_model,
        exposure=exposure,
        runtime=RuntimeConfig(load_settings(Path("configs/smoke.yaml"))),
        ledger=ledger,
    )
    elections.repo.elections["el_fixture"] = Election(
        "el_fixture",
        "president" if seats == 1 else "council",
        seats,
        "plurality",
        0,
        10,
        9,
        (),
    )
    return elections


def test_plurality_tie_is_deterministic_and_records_tie_break() -> None:
    elections = _office()
    ballots = (Ballot("v1", "ca_a"), Ballot("v2", "ca_b"))

    first = elections.tally("el_fixture", ballots, "plurality")
    second = elections.tally("el_fixture", tuple(reversed(ballots)), "plurality")

    assert first == second
    assert first.winner_ids[0] in {"ca_a", "ca_b"}
    assert first.rounds == ({"tie_break_candidates": 2},)


def test_approval_and_irv_produce_winners_and_irv_records_rounds() -> None:
    elections = _office()
    approval = elections.tally(
        "el_fixture",
        (
            Ballot("v1", None, approvals=("ca_a", "ca_b")),
            Ballot("v2", None, approvals=("ca_a",)),
        ),
        "approval",
    )
    irv = elections.tally(
        "el_fixture",
        (
            Ballot("v1", None, ranking=("ca_a", "ca_b", "ca_c")),
            Ballot("v2", None, ranking=("ca_b", "ca_c", "ca_a")),
            Ballot("v3", None, ranking=("ca_c", "ca_b", "ca_a")),
        ),
        "irv",
    )

    assert approval.winner_ids == ("ca_a",)
    assert irv.winner_ids
    assert len(irv.rounds) >= 2


def test_proportional_uses_dhondt_seat_allocation() -> None:
    elections = _office(seats=3)
    for candidacy_id, party_id in (
        ("ca_a1", "pt_a"),
        ("ca_a2", "pt_a"),
        ("ca_b1", "pt_b"),
        ("ca_b2", "pt_b"),
    ):
        elections.repo.candidacies[candidacy_id] = Candidacy(
            candidacy_id,
            "el_fixture",
            f"ag_{candidacy_id}",
            party_id,
            {},
            0,
            0,
        )
    ballots = tuple(
        Ballot(f"va_{index}", "ca_a1" if index < 4 else "ca_a2") for index in range(6)
    ) + tuple(Ballot(f"vb_{index}", "ca_b1" if index < 2 else "ca_b2") for index in range(3))

    result = elections.tally("el_fixture", ballots, "proportional")

    assert result.rounds == ({"pt_a": 2, "pt_b": 1},)
    assert result.winner_ids == ("ca_a1", "ca_a2", "ca_b1")


def test_turnout_excludes_abstention_ballots() -> None:
    elections = _office()
    elections.repo.eligible_counts["el_fixture"] = 4
    elections.repo.ballots[("el_fixture", "ag_voter")] = Ballot("ag_voter", "ca_one")
    elections.repo.ballots[("el_fixture", "ag_abstainer")] = Ballot("ag_abstainer", None)

    assert elections.turnout("el_fixture") == 0.25


@pytest.mark.asyncio
async def test_deposit_refunds_are_one_audited_batch_transaction() -> None:
    elections = _office()
    elections.repo.candidacies["ca_one"] = Candidacy(
        "ca_one",
        "el_fixture",
        "ag_candidate",
        None,
        {},
        0,
        0,
    )
    elections.repo.deposits["ca_one"] = ("ag_candidate", 100, False)
    elections.repo.ballots[("el_fixture", "ag_voter")] = Ballot("ag_voter", "ca_one")
    elections.repo.eligible_counts["el_fixture"] = 1

    events = await elections.hold("el_fixture", 10)

    refund = next(event for event in events if event.kind == CANDIDACY_DEPOSITS_REFUNDED)
    assert refund.payload["txn_id"]
    assert refund.payload["refunds"] == [
        {
            "candidacy_id": "ca_one",
            "agent_id": "ag_candidate",
            "amount_cents": 100,
        }
    ]
    assert elections.repo.deposits["ca_one"] == ("ag_candidate", 100, True)


class FailingRefundLedger(NullPolityLedger):
    def post_transfers(self, *args, **kwargs) -> str:
        del args, kwargs
        raise RuntimeError("refund failed")


@pytest.mark.asyncio
async def test_failed_deposit_refund_restores_election_state() -> None:
    elections = _office(ledger=FailingRefundLedger())
    elections.repo.candidacies["ca_one"] = Candidacy(
        "ca_one",
        "el_fixture",
        "ag_candidate",
        None,
        {},
        0,
        0,
    )
    elections.repo.deposits["ca_one"] = ("ag_candidate", 100, False)
    elections.repo.ballots[("el_fixture", "ag_voter")] = Ballot("ag_voter", "ca_one")
    elections.repo.eligible_counts["el_fixture"] = 1

    with pytest.raises(RuntimeError, match="refund failed"):
        await elections.hold("el_fixture", 10)

    assert elections.repo.deposits["ca_one"] == ("ag_candidate", 100, False)
    assert not elections.repo.elections["el_fixture"].resolved
    assert elections.log.staged() == ()

from types import SimpleNamespace

from polis.agents.actions import ActionType, GateFailure, ValidationContext, make_action
from polis.agents.actions.params.polity import AnnounceCandidacyParams
from polis.society.polity import Ballot
from tests.unit.society.test_polity_resolver import _ctx, _resolver


def _open_election():
    resolver, _runtime = _resolver()
    resolver.elections.agents.update(
        {
            "ag_candidate": SimpleNamespace(
                age_years=35,
                alive=True,
                incarcerated=False,
                resident_since_tick=0,
                criminal_record=(),
            ),
            "ag_voter": SimpleNamespace(
                age_years=25,
                alive=True,
                incarcerated=False,
                resident_since_tick=0,
                criminal_record=(),
            ),
        }
    )
    election_id, _event = resolver.elections.call("president", tick=2_000)
    return resolver, election_id


def test_independent_candidacy_is_legal_and_vote_is_unique() -> None:
    resolver, election_id = _open_election()
    candidacy, _events = resolver.elections.announce(
        "ag_candidate",
        AnnounceCandidacyParams(election_id=election_id, platform={}),
        2_001,
    )

    assert candidacy.party_id is None
    voting_tick = resolver.elections.repo.elections[election_id].voting_tick
    resolver.elections.cast(
        election_id,
        Ballot("ag_voter", candidacy.candidacy_id),
        voting_tick,
    )
    duplicate = make_action(
        actor_id="ag_voter",
        tick=voting_tick,
        action_type=ActionType.VOTE,
        params={"election_id": election_id, "candidacy_id": candidacy.candidacy_id},
    )
    failure = resolver.check_capability(duplicate, _ctx(voting_tick))
    assert isinstance(failure, GateFailure)
    assert failure.reason == "capability"
    assert "already voted" in failure.detail


def test_age_and_record_bar_fail_at_capability() -> None:
    resolver, election_id = _open_election()
    action = make_action(
        actor_id="ag_candidate",
        tick=2_001,
        action_type=ActionType.ANNOUNCE_CANDIDACY,
        params={"election_id": election_id, "platform": {}},
    )

    context = _ctx(2_001)
    underage = resolver.check_capability(
        action,
        ValidationContext(
            observation=context.observation,
            state=SimpleNamespace(
                age_years=17,
                alive=True,
                incarcerated=False,
                criminal_record=(),
            ),
            tick=2_001,
        ),
    )
    barred = resolver.check_capability(
        action,
        ValidationContext(
            observation=context.observation,
            state=SimpleNamespace(
                age_years=35,
                alive=True,
                incarcerated=False,
                criminal_record=("fraud",),
            ),
            tick=2_001,
        ),
    )

    assert isinstance(underage, GateFailure)
    assert isinstance(barred, GateFailure)

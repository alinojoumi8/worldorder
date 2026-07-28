from types import SimpleNamespace

from polis.agents.actions.params.polity import CampaignParams, FoundPartyParams
from polis.society.polity import Candidacy
from tests.unit.society.test_polity_resolver import _resolver


class ReachWorld:
    def __init__(self, count: int) -> None:
        self.locations = {f"ag_{index:05}": "pl_square" for index in range(count - 1, -1, -1)}

    def occupancy(self, place_id: str):
        assert place_id == "pl_square"
        return ("ag_c", "ag_a", "ag_b")

    def place(self, place_id: str):
        assert place_id == "pl_square"
        return SimpleNamespace(owner_id="ag_venue")


class ReachOutlets:
    def __init__(self, reach: int) -> None:
        self.outlet = SimpleNamespace(reach=reach, firm_id="fm_media")

    def get(self, outlet_id: str):
        return self.outlet if outlet_id == "ol_daily" else None

    def live(self):
        return (self.outlet,)


def test_ad_reach_formula_is_sorted_and_payload_is_capped() -> None:
    resolver, _runtime = _resolver()
    elections = resolver.elections
    elections.world = ReachWorld(10_001)
    elections.outlets = ReachOutlets(20_000)
    elections.repo.candidacies["ca_one"] = Candidacy(
        "ca_one",
        "el_one",
        "ag_candidate",
        None,
        {},
        0,
        0,
    )

    event = elections.campaign(
        "ag_candidate",
        CampaignParams(
            candidacy_id="ca_one",
            amount_cents=1_000,
            channel="ads",
            target_id="ol_daily",
        ),
        2,
    )[0]

    assert event.payload["reach"] == 10_001
    assert len(event.payload["reached_agent_ids"]) == 10_000
    assert event.payload["reached_agent_ids"] == sorted(event.payload["reached_agent_ids"])
    assert elections.repo.candidacies["ca_one"].spend_cents == 1_000


def test_rally_uses_current_occupancy_and_canvass_uses_party_members() -> None:
    resolver, _runtime = _resolver()
    elections = resolver.elections
    elections.world = ReachWorld(3)
    party, _events = resolver.parties.found(
        "ag_candidate",
        FoundPartyParams(
            name="Civic",
            platform={},
            founding_member_ids=("ag_candidate", "ag_b", "ag_a"),
        ),
        1,
    )
    resolver.parties.join("ag_b", party.party_id, 1)
    resolver.parties.join("ag_a", party.party_id, 1)
    elections.repo.candidacies["ca_one"] = Candidacy(
        "ca_one",
        "el_one",
        "ag_candidate",
        party.party_id,
        {},
        0,
        0,
    )

    rally = elections.campaign(
        "ag_candidate",
        CampaignParams(
            candidacy_id="ca_one",
            amount_cents=100,
            channel="rally",
            place_id="pl_square",
        ),
        2,
    )[0]
    canvass = elections.campaign(
        "ag_candidate",
        CampaignParams(candidacy_id="ca_one", channel="canvass"),
        3,
    )[0]

    assert rally.payload["reached_agent_ids"] == ["ag_a", "ag_b", "ag_c"]
    assert canvass.payload["reached_agent_ids"] == ["ag_a", "ag_b"]
    assert canvass.payload["amount_cents"] == 0

from uuid import UUID

from polis.agents.actions.params.polity import FoundPartyParams
from polis.config.settings import PolitySettings
from polis.events.kinds import PARTY_DISSOLVED, PARTY_PLATFORM_CHANGED
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.society.polity import PartyRegistry


class Beliefs:
    def __init__(self, values: dict[str, float] | None = None) -> None:
        self.values = values or {}

    def value(self, agent_id: str, proposition: str) -> float:
        del proposition
        return self.values.get(agent_id, 0.0)

    def confidence(self, agent_id: str, proposition: str) -> float:
        del agent_id, proposition
        return 1.0


def _registry(
    *,
    values: dict[str, float] | None = None,
    drift_mode: str = "member_mean",
) -> PartyRegistry:
    return PartyRegistry(
        log=EventLog(UUID(int=1820), MemoryEventSink()),
        clock=Clock(PROFILES["microscope"]),
        beliefs=Beliefs(values),
        cfg=PolitySettings(party_founding_fee_cents=0),
        drift_mode=drift_mode,  # type: ignore[arg-type]
    )


def _found(registry: PartyRegistry, tick: int = 1) -> str:
    party, _events = registry.found(
        "ag_a",
        FoundPartyParams(
            name="Civic",
            platform={"tax.vat.should_rise": 0.0},
            founding_member_ids=("ag_a", "ag_b", "ag_c"),
        ),
        tick,
    )
    return party.party_id


def test_parties_begin_empty_and_unratified_founding_dissolves() -> None:
    registry = _registry()
    assert registry.live() == ()
    assert registry.founding_attempts == 0

    party_id = _found(registry)
    assert registry.dissolve_stale(2) == ()
    events = registry.dissolve_stale(3)

    assert registry.founding_attempts == 1
    assert [event.kind for event in events] == [PARTY_DISSOLVED]
    assert registry.get(party_id).dissolved_tick == 3  # type: ignore[union-attr]


def test_three_members_ratify_and_leader_ties_by_agent_id() -> None:
    registry = _registry()
    party_id = _found(registry)
    registry.join("ag_b", party_id, 1)
    registry.join("ag_c", party_id, 1)

    party = registry.get(party_id)
    assert party is not None
    assert party.member_ids == ("ag_a", "ag_b", "ag_c")
    assert party.leader_id == "ag_a"
    assert registry.dissolve_stale(3) == ()


def test_below_three_members_dissolves_after_thirty_sim_days() -> None:
    registry = _registry()
    party_id = _found(registry)
    registry.join("ag_b", party_id, 1)
    registry.join("ag_c", party_id, 1)
    registry.leave("ag_c", "resigned", 5)
    thirty_days = 30 * PROFILES["microscope"].ticks_per_sim_day

    assert registry.dissolve_stale(5 + thirty_days - 1) == ()
    assert registry.dissolve_stale(5 + thirty_days)[0].kind == PARTY_DISSOLVED


def test_platform_drift_and_fixed_ablation() -> None:
    values = {"ag_a": -1.0, "ag_b": 0.0, "ag_c": 1.0}
    registry = _registry(values=values)
    party_id = _found(registry)
    registry.join("ag_b", party_id, 1)
    registry.join("ag_c", party_id, 1)

    events = registry.drift_platforms(2)

    assert events == ()
    assert registry.get(party_id).platform["tax.vat.should_rise"] == 0.0  # type: ignore[union-attr]

    registry = _registry(values={"ag_a": 1.0, "ag_b": 1.0, "ag_c": 1.0})
    party_id = _found(registry)
    registry.join("ag_b", party_id, 1)
    registry.join("ag_c", party_id, 1)
    assert registry.drift_platforms(2)[0].kind == PARTY_PLATFORM_CHANGED
    assert registry.get(party_id).platform["tax.vat.should_rise"] == 0.25  # type: ignore[union-attr]

    fixed = _registry(values=values, drift_mode="fixed")
    fixed_party = _found(fixed)
    fixed.join("ag_b", fixed_party, 1)
    fixed.join("ag_c", fixed_party, 1)
    assert fixed.drift_platforms(2) == ()

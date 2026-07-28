from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from polis.config.runtime import RuntimeConfig
from polis.config.settings import PolitySettings, load_settings
from polis.events.kinds import POLICY_BLOCKED, POLICY_VOTED
from polis.events.log import EventLog, MemoryEventSink
from polis.kernel.clock import PROFILES, Clock
from polis.society.policy import (
    FiscalProjector,
    FiscalSnapshot,
    MemoryPolicyRepository,
    PolicyEngine,
    Proposal,
)


class Offices:
    def __init__(self, held: str | None = None) -> None:
        self.held = held

    def holds_office(self, agent_id: str, tick: int) -> str | None:
        del agent_id, tick
        return self.held

    def holder(self, office: str, tick: int) -> str | tuple[str, ...] | None:
        del office, tick
        return None


def _proposal(parameter: str, value: Any, *, number: int = 1) -> Proposal:
    return Proposal(
        f"pr_{number}",
        "ag_proposer",
        parameter,
        None,
        value,
        "test",
        (),
        1,
    )


def _engine(
    *,
    held: str | None = None,
    snapshot: FiscalSnapshot | None = None,
) -> PolicyEngine:
    settings = load_settings(Path("configs/smoke.yaml"))
    return PolicyEngine(
        runtime=RuntimeConfig(settings),
        log=EventLog(UUID(int=1819), MemoryEventSink()),
        clock=Clock(PROFILES["microscope"]),
        offices=Offices(held),
        fiscal=FiscalProjector(lambda _overlay, _horizon, _tick: snapshot or FiscalSnapshot()),
        repo=MemoryPolicyRepository(),
        cfg=PolitySettings(),
    )


@pytest.mark.parametrize(
    ("engine_kwargs", "proposal", "failed"),
    (
        ({}, _proposal("clock.demographic_acceleration", "bad"), "P-SCOPE"),
        ({}, _proposal("tax.vat_bp", 10_001), "P-RANGE"),
        ({}, _proposal("prison.capacity", -1), "P-NONNEGATIVE"),
        (
            {},
            _proposal("tax.income.brackets", ((100, 1_000), (0, 2_000))),
            "P-MONOTONE",
        ),
        (
            {"held": "president"},
            _proposal("money.policy_rate_bp", 100),
            "P-SEPARATION",
        ),
        (
            {"snapshot": FiscalSnapshot(money_delta_cents=1)},
            _proposal("tax.vat_bp", 1_500),
            "P-MONEY",
        ),
        (
            {"snapshot": FiscalSnapshot(current_balance_cents=-600_000_000)},
            _proposal("tax.vat_bp", 1_500),
            "P-SOLVENCY",
        ),
    ),
)
def test_admissibility_predicates_are_isolated_and_ordered(
    engine_kwargs: dict[str, Any],
    proposal: Proposal,
    failed: str,
) -> None:
    result = _engine(**engine_kwargs).admissible(proposal, 2)
    assert not result.admissible
    assert result.failed == failed


def test_first_failure_wins() -> None:
    engine = _engine(snapshot=FiscalSnapshot(money_delta_cents=1))
    proposal = _proposal("prison.capacity", -1)

    assert engine.admissible(proposal, 2).failed == "P-NONNEGATIVE"


@pytest.mark.asyncio
async def test_blocked_proposal_emits_only_block_before_closing() -> None:
    engine = _engine()
    engine.propose(_proposal("clock.demographic_acceleration", 2))

    events = await engine.council_session(2)

    assert [event.kind for event in events] == [POLICY_BLOCKED]
    assert events[0].payload["predicate"] == "P-SCOPE"
    assert all(event.kind != POLICY_VOTED for event in events)
    assert engine.repo.pending() == ()

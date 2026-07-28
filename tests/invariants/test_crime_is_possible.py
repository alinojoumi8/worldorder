from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args
from uuid import UUID

from polis.agents.actions import (
    Action,
    ActionType,
    ActionValidator,
    InstitutionSlot,
    ResolutionContext,
    ResolverRegistry,
    SlotLedger,
    ValidatedAction,
    ValidationContext,
    make_action,
)
from polis.agents.actions.types import RejectReason
from polis.config.settings import load_settings
from polis.events.kinds import (
    ACTION_FLAGGED_ILLEGAL,
    ACTION_SUBMITTED,
    CRIME_COMMITTED,
    LEGALITY_FLAGGED,
)
from polis.events.log import EventLog, MemoryEventSink
from polis.events.types import Event, NewEvent
from polis.society.law import (
    LawLegalityOracle,
    MemoryCrimeRepository,
    MnpiIndex,
    ObligationIndex,
)
from tests.law_support import Memories, checker, clock, law_cfg, runtime


class LawResolver:
    slot = InstitutionSlot.LAW
    handles = frozenset({ActionType.COMMIT_CRIME})

    def check_capability(
        self,
        action: Action,
        ctx: ValidationContext,
    ) -> None:
        del action, ctx

    def check_locality(
        self,
        action: Action,
        ctx: ValidationContext,
    ) -> None:
        del action, ctx

    def check_resources(
        self,
        action: Action,
        ctx: ValidationContext,
    ) -> None:
        del action, ctx

    def __init__(self) -> None:
        self.seen: list[ValidatedAction] = []

    def resolve(
        self,
        actions: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> Sequence[Event]:
        del tick, ctx
        self.seen.extend(actions)
        return ()

    def options_for(
        self,
        action_type: ActionType,
        ctx: ValidationContext,
    ) -> tuple[Mapping[str, Any], ...]:
        del action_type, ctx
        return ()


def test_flagged_crime_proceeds_to_the_law_resolver() -> None:
    registry = ResolverRegistry()
    resolver = LawResolver()
    registry.register(resolver)
    drafts: list[NewEvent] = []
    law_sink = MemoryEventSink()
    law_log = EventLog(UUID(int=1919), law_sink)
    memories = Memories()
    configured_clock = clock()
    configured_checker = checker(law_log)
    configured_oracle = LawLegalityOracle(
        log=law_log,
        clock=configured_clock,
        runtime=runtime(),
        mnpi=MnpiIndex(
            memories=memories,
            cfg=law_cfg(),
            clock=configured_clock,
            events=(),
        ),
        obligations=ObligationIndex(),
        checker=configured_checker,
        memories=memories,
        repo=MemoryCrimeRepository(),
        cfg=law_cfg(),
    )

    def emit(draft: NewEvent) -> Event:
        drafts.append(draft)
        unexpected_kind = draft.kind not in {
            ACTION_SUBMITTED,
            ACTION_FLAGGED_ILLEGAL,
        }
        assert not unexpected_kind
        seq = len(drafts)
        return Event(
            seq=seq,
            run_id=UUID("10000000-0000-0000-0000-000000000001"),
            tick=1,
            sim_time=datetime(2100, 1, 1, tzinfo=UTC),
            kind=draft.kind,
            actor_id=draft.actor_id,
            subject_ids=draft.subject_ids,
            cause_seq=draft.cause_seq,
            payload=draft.payload,
            sig=None,
            prev_hash="0" * 64,
            hash=f"{seq:064x}",
        )

    validator = ActionValidator(
        registry,
        SlotLedger(1),
        oracle=configured_oracle,
        emit=emit,
    )
    action = make_action(
        actor_id="ag_offender",
        tick=1,
        action_type=ActionType.COMMIT_CRIME,
        params={
            "crime_type": "theft",
            "victim_id": "ag_victim",
            "amount_cents": 500,
        },
        origin="deliberate",
    )
    result = validator.validate(
        action,
        ValidationContext(observation=object(), state=object(), tick=1),
    )

    assert isinstance(result, ValidatedAction)
    assert result.legality.is_crime
    assert [draft.kind for draft in drafts] == [
        ACTION_SUBMITTED,
        ACTION_FLAGGED_ILLEGAL,
    ]
    assert drafts[-1].payload["proceeded"] is True
    resolver.resolve(
        (result,),
        1,
        ResolutionContext(emit=lambda draft: emit(draft)),
    )
    assert resolver.seen == [result]
    assert [event.kind for event in law_log.staged()] == [
        LEGALITY_FLAGGED,
        CRIME_COMMITTED,
    ]


def test_m4_baseline_requires_the_law_oracle_and_legality_never_rejects() -> None:
    settings = load_settings(Path("configs/baseline.yaml"))

    assert settings.actions.legality.oracle == "law"
    assert "legality" not in get_args(RejectReason)

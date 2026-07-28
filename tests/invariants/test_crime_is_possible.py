from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from polis.agents.actions import (
    Action,
    ActionType,
    ActionValidator,
    InstitutionSlot,
    LegalityVerdict,
    ResolutionContext,
    ResolverRegistry,
    SlotLedger,
    ValidatedAction,
    ValidationContext,
    make_action,
)
from polis.agents.actions.params.base import ActionParams
from polis.events.kinds import ACTION_FLAGGED_ILLEGAL, ACTION_SUBMITTED
from polis.events.types import Event, NewEvent


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

    def resolve(
        self,
        actions: Sequence[ValidatedAction],
        tick: int,
        ctx: ResolutionContext,
    ) -> Sequence[Event]:
        del actions, tick, ctx
        return ()

    def options_for(
        self,
        action_type: ActionType,
        ctx: ValidationContext,
    ) -> tuple[Mapping[str, Any], ...]:
        del action_type, ctx
        return ()


class CrimeOracle:
    def assess(
        self,
        action: Action,
        params: ActionParams,
        ctx: ValidationContext,
    ) -> LegalityVerdict:
        del action, params, ctx
        return LegalityVerdict(
            is_crime=True,
            crime_type="theft",
            victim_id="ag_victim",
            amount_cents=500,
            crime_id="cr_test",
        )


def test_flagged_crime_proceeds_to_the_law_resolver() -> None:
    registry = ResolverRegistry()
    registry.register(LawResolver())
    drafts: list[NewEvent] = []

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
        oracle=CrimeOracle(),
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

from __future__ import annotations

from pydantic import Field

from polis.agents.actions.params.base import ActionParams


class EnrolParams(ActionParams):
    institution_id: str
    programme_id: str


class StudyParams(ActionParams):
    enrolment_id: str | None = None
    effort_bp: int = Field(default=10_000, ge=0, le=10_000)


class DropOutParams(ActionParams):
    enrolment_id: str
    reason: str | None = None


class TakeExamParams(ActionParams):
    enrolment_id: str
    exam_id: str

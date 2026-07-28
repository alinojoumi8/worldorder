from __future__ import annotations

from pydantic import Field

from polis.agents.actions.params.base import ActionParams, Cents


class ApplyForJobParams(ActionParams):
    vacancy_id: str
    asked_wage_cents: Cents | None = None


class AcceptOfferParams(ActionParams):
    offer_id: str
    reason: str | None = None


class DeclineOfferParams(ActionParams):
    offer_id: str
    reason: str | None = None


class QuitJobParams(ActionParams):
    employment_id: str
    reason: str | None = None


class NegotiateWageParams(ActionParams):
    offer_id: str | None = None
    employment_id: str | None = None
    counter_cents: Cents


class PostVacancyParams(ActionParams):
    firm_id: str
    occupation: str
    wage_offer_cents: Cents
    headcount: int = Field(ge=1)


class MakeOfferParams(ActionParams):
    application_id: str
    wage_cents: Cents


class FireEmployeeParams(ActionParams):
    employment_id: str
    reason: str | None = None


class WorkParams(ActionParams):
    employment_id: str
    effort_bp: int = Field(default=10_000, ge=0, le=10_000)

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from polis.agents.actions.params.base import ActionParams, Cents
from polis.agents.actions.params.media import ClaimParams

CrimeType = Literal[
    "theft",
    "fraud",
    "insider_trading",
    "assault",
    "contract_breach",
    "embezzlement",
    "perjury",
]


class CommitCrimeParams(ActionParams):
    crime_type: CrimeType
    victim_id: str | None = None
    amount_cents: Cents | None = None


class ReportCrimeParams(ActionParams):
    crime_id: str | None = None
    accused_id: str | None = None
    suspect_id: str | None = None
    crime_type: CrimeType | None = None
    description: str | None = None
    evidence_event_seqs: tuple[int, ...] = ()

    @model_validator(mode="after")
    def require_report(self) -> ReportCrimeParams:
        if self.crime_id is None and self.description is None:
            raise ValueError("crime_id or description is required")
        return self


class FileSuitParams(ActionParams):
    case_type: Literal["criminal", "civil"] = "civil"
    defendant_id: str
    claim: str | None = None
    cause_of_action: str | None = None
    amount_cents: Cents | None = None
    claim_cents: Cents | None = None
    crime_id: str | None = None
    evidence_event_seqs: tuple[int, ...] = ()

    @model_validator(mode="after")
    def normalise_cause(self) -> FileSuitParams:
        cause = self.cause_of_action or self.claim
        if not cause:
            raise ValueError("cause_of_action or claim is required")
        object.__setattr__(self, "cause_of_action", cause)
        if self.claim_cents is None and self.amount_cents is not None:
            object.__setattr__(self, "claim_cents", self.amount_cents)
        return self


class RetainCounselParams(ActionParams):
    lawyer_id: str | None = None
    counsel_id: str | None = None
    case_id: str
    fee_cents: Cents | None = None

    @model_validator(mode="after")
    def require_counsel(self) -> RetainCounselParams:
        counsel = self.counsel_id or self.lawyer_id
        if counsel is None:
            raise ValueError("counsel_id or lawyer_id is required")
        object.__setattr__(self, "counsel_id", counsel)
        return self


class TestifyParams(ActionParams):
    case_id: str
    statement: str
    claims: tuple[ClaimParams, ...] = ()


class SettleParams(ActionParams):
    case_id: str
    amount_cents: Cents


class RuleParams(ActionParams):
    case_id: str
    ruling: str = ""

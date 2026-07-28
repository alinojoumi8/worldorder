from __future__ import annotations

from typing import Literal

from polis.agents.actions.params.base import ActionParams, Cents

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
    crime_type: CrimeType | None = None


class FileSuitParams(ActionParams):
    defendant_id: str
    claim: str
    amount_cents: Cents | None = None


class RetainCounselParams(ActionParams):
    lawyer_id: str
    case_id: str | None = None
    fee_cents: Cents | None = None


class TestifyParams(ActionParams):
    case_id: str
    statement: str


class SettleParams(ActionParams):
    case_id: str
    amount_cents: Cents


class RuleParams(ActionParams):
    case_id: str
    ruling: str

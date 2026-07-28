from __future__ import annotations

from polis.agents.actions.params.base import ActionParams, Cents, PositiveCents


class OpenAccountParams(ActionParams):
    bank_id: str
    amount_cents: Cents | None = None


class DepositParams(ActionParams):
    bank_id: str
    amount_cents: Cents | None = None


class WithdrawParams(ActionParams):
    bank_id: str
    amount_cents: Cents | None = None


class ApplyForLoanParams(ActionParams):
    loan_id: str | None = None
    bank_id: str | None = None
    amount_cents: PositiveCents


class RepayLoanParams(ActionParams):
    loan_id: str | None = None
    bank_id: str | None = None
    amount_cents: PositiveCents


class DefaultParams(ActionParams):
    loan_id: str | None = None
    bank_id: str | None = None
    amount_cents: PositiveCents

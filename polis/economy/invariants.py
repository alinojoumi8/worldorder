from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from polis.economy.ledger import Ledger, account_id
from polis.kernel.invariants import Ok, Result, Severity, Violation


class EconomyView(Protocol):
    def cached_net_worth_cents(self) -> Mapping[str, int]: ...


def m0_cents(ledger: Ledger) -> int:
    return sum(
        account.balance_cents
        for account in ledger.accounts()
        if account.code in {"cash", "res"}
        or (
            account.code == "dep"
            and account.owner_id == "gv_treasury"
            and account.bank_id == "bk_cb"
        )
    )


def m1_cents(ledger: Ledger) -> int:
    currency = sum(
        account.balance_cents
        for account in ledger.accounts()
        if account.code == "cash" and account.owner_type not in {"bank", "central_bank"}
    )
    deposits = -sum(
        account.balance_cents
        for account in ledger.accounts()
        if account.code == "dpl" and account.owner_id != "bk_cb"
    )
    government_commercial_deposits = sum(
        account.balance_cents
        for account in ledger.accounts()
        if account.code == "dep"
        and account.owner_id == "gv_treasury"
        and account.bank_id != "bk_cb"
    )
    return currency + deposits - government_commercial_deposits


def check_ledger(ledger: Ledger) -> Result:
    global_imbalance = ledger.global_balance_cents()
    materialisation = ledger.materialisation_imbalance_cents()
    if global_imbalance == 0 and materialisation == 0:
        return Ok("INV-LEDGER")
    return Violation(
        "INV-LEDGER",
        "global=0, materialisation=0",
        f"global={global_imbalance}, materialisation={materialisation}",
        {
            "M-2": global_imbalance,
            "M-3": materialisation,
        },
        Severity.HALT,
    )


def check_money(ledger: Ledger, view: EconomyView | None = None) -> Result:
    failures: dict[str, object] = {}
    global_imbalance = ledger.global_balance_cents()
    if global_imbalance != 0:
        failures["M-2"] = global_imbalance
    materialisation = ledger.materialisation_imbalance_cents()
    if materialisation != 0:
        failures["M-3"] = materialisation
    base_money = ledger.base_money_imbalance_cents()
    if base_money != 0:
        failures["M-4"] = base_money
    deposit_imbalances = {
        bank_id: amount for bank_id, amount in ledger.deposit_imbalances().items() if amount != 0
    }
    if deposit_imbalances:
        failures["M-5"] = deposit_imbalances
    if view is not None:
        denormalised = {
            owner_id: cached - ledger.net_worth(owner_id)
            for owner_id, cached in sorted(view.cached_net_worth_cents().items())
            if cached != ledger.net_worth(owner_id)
        }
        if denormalised:
            failures["M-6"] = denormalised
    if not failures:
        return Ok("INV-MONEY")
    first = next(iter(failures))
    return Violation(
        "INV-MONEY",
        "M-2..M-6 exact",
        f"{first} failed",
        failures,
        Severity.HALT,
    )


def issued_base_money_cents(ledger: Ledger) -> int:
    issuance = account_id("iss", "bk_cb")
    return -ledger.balance(issuance) if ledger.is_open(issuance) else 0

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Final, Literal, Protocol, cast
from uuid import UUID, uuid5

from polis.config.errors import PolisError
from polis.events.types import Event

AccountCode = Literal["cash", "dep", "esc", "res", "lnr", "txr", "dpl", "lnp", "iss"]
Polarity = Literal["asset", "liability"]

POLARITY: Final[Mapping[AccountCode, Polarity]] = {
    "cash": "asset",
    "dep": "asset",
    "esc": "asset",
    "res": "asset",
    "lnr": "asset",
    "txr": "asset",
    "dpl": "liability",
    "lnp": "liability",
    "iss": "liability",
}
ACCOUNT_TYPE: Final[Mapping[AccountCode, str]] = {
    "cash": "cash",
    "dep": "deposit",
    "esc": "escrow",
    "res": "reserve",
    "lnr": "loan_receivable",
    "txr": "tax_receivable",
    "dpl": "deposit",
    "lnp": "loan_payable",
    "iss": "issuance",
}
REASONS: Final = frozenset(
    {
        "transfer",
        "wage",
        "purchase",
        "trade",
        "loan",
        "interest",
        "dividend",
        "tax",
        "inheritance",
        "fine",
        "write_off",
        "escrow",
        "issuance",
        "withdrawal",
    }
)


class LedgerError(PolisError):
    """A double-entry precondition or accounting identity failed."""


@dataclass(frozen=True, slots=True)
class Leg:
    account_id: str
    direction: int
    amount_cents: int
    reason: str


@dataclass(slots=True)
class Account:
    account_id: str
    code: AccountCode
    owner_id: str
    owner_type: str
    bank_id: str | None
    ref: str | None
    currency: str
    opened_tick: int
    closed_tick: int | None = None
    balance_cents: int = 0
    entry_balance_cents: int = 0


@dataclass(frozen=True, slots=True)
class Entry:
    txn_id: UUID
    tick: int
    event_seq: int
    account_id: str
    direction: int
    amount_cents: int
    reason: str


class LedgerRepository(Protocol):
    async def flush(
        self,
        accounts: Sequence[Mapping[str, Any]],
        entries: Sequence[Mapping[str, Any]],
    ) -> None: ...


def account_id(
    code: AccountCode,
    owner_id: str,
    *,
    bank_id: str | None = None,
    ref: str | None = None,
) -> str:
    if code not in POLARITY:
        raise LedgerError(f"unknown account code: {code}")
    if not owner_id or any(marker in owner_id for marker in (":", "@", "#")):
        raise LedgerError(f"invalid account owner: {owner_id!r}")
    if bank_id is not None and (
        not bank_id or any(marker in bank_id for marker in (":", "@", "#"))
    ):
        raise LedgerError(f"invalid bank id: {bank_id!r}")
    if ref is not None and (not ref or any(marker in ref for marker in (":", "@", "#"))):
        raise LedgerError(f"invalid account reference: {ref!r}")
    if code in {"dep", "esc"} and bank_id is None:
        raise LedgerError(f"{code} account requires bank_id")
    if code not in {"dep", "esc"} and bank_id is not None:
        raise LedgerError(f"{code} account cannot specify bank_id")
    if code in {"lnr", "lnp", "esc"} and ref is None:
        raise LedgerError(f"{code} account requires ref")
    if code not in {"lnr", "lnp", "esc"} and ref is not None:
        raise LedgerError(f"{code} account cannot specify ref")
    value = f"{code}:{owner_id}"
    if bank_id is not None:
        value += f"@{bank_id}"
    if ref is not None:
        value += f"#{ref}"
    return value


def parse_account_id(value: str) -> tuple[AccountCode, str, str | None, str | None]:
    try:
        code_text, remainder = value.split(":", 1)
    except ValueError as exc:
        raise LedgerError(f"invalid account id: {value!r}") from exc
    if code_text not in POLARITY:
        raise LedgerError(f"unknown account code in {value!r}")
    owner_and_bank, separator, ref = remainder.partition("#")
    owner, bank_separator, bank = owner_and_bank.partition("@")
    parsed_ref = ref if separator else None
    parsed_bank = bank if bank_separator else None
    code = cast(AccountCode, code_text)
    canonical = account_id(code, owner, bank_id=parsed_bank, ref=parsed_ref)
    if canonical != value:
        raise LedgerError(f"non-canonical account id: {value!r}")
    return code, owner, parsed_bank, parsed_ref


def bank_of(value: str) -> str | None:
    code, owner, bank_id, _ref = parse_account_id(value)
    if code in {"dep", "esc"}:
        return bank_id
    if code in {"dpl", "res"}:
        return owner
    return None


class Ledger:
    name = "ledger"

    def __init__(
        self,
        run_id: UUID,
        repo: LedgerRepository | None = None,
    ) -> None:
        self.run_id = run_id
        self.repo = repo
        self._accounts: dict[str, Account] = {}
        self._entries: list[Entry] = []
        self._pending_entries: list[Entry] = []
        self._dirty_accounts: set[str] = set()
        self._ordinal_by_tick: dict[int, int] = defaultdict(int)
        self._posted_by_tick: dict[int, list[UUID]] = defaultdict(list)

    def open_account(
        self,
        code: AccountCode,
        owner_id: str,
        owner_type: str,
        *,
        bank_id: str | None = None,
        ref: str | None = None,
        tick: int,
    ) -> str:
        if tick < 0:
            raise LedgerError("account opened_tick must be non-negative")
        value = account_id(code, owner_id, bank_id=bank_id, ref=ref)
        if value in self._accounts:
            raise LedgerError(f"account already exists: {value}")
        if code == "iss" and (owner_id != "bk_cb" or owner_type != "central_bank"):
            raise LedgerError("issuance account is reserved for the central bank")
        self._accounts[value] = Account(
            value,
            code,
            owner_id,
            owner_type,
            bank_id,
            ref,
            "POL",
            tick,
        )
        self._dirty_accounts.add(value)
        return value

    def close_account(self, value: str, *, tick: int) -> None:
        account = self._open_account(value)
        if account.balance_cents != 0:
            raise LedgerError(f"cannot close non-zero account: {value}")
        if tick < account.opened_tick:
            raise LedgerError("closed_tick precedes opened_tick")
        account.closed_tick = tick
        self._dirty_accounts.add(value)

    def is_open(self, value: str) -> bool:
        account = self._accounts.get(value)
        return account is not None and account.closed_tick is None

    def balance(self, value: str) -> int:
        return self._account(value).balance_cents

    def net_worth(self, owner_id: str) -> int:
        return sum(
            account.balance_cents
            for account in self._accounts.values()
            if account.owner_id == owner_id
        )

    def liquid(self, owner_id: str) -> int:
        return sum(
            account.balance_cents
            for account in self._accounts.values()
            if account.owner_id == owner_id and account.code in {"cash", "dep"}
        )

    def accounts_of(self, owner_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                account.account_id
                for account in self._accounts.values()
                if account.owner_id == owner_id
            )
        )

    def accounts(self) -> tuple[Account, ...]:
        return tuple(self._accounts[key] for key in sorted(self._accounts))

    def entries(self) -> tuple[Entry, ...]:
        return tuple(self._entries)

    def next_txn_id(self, tick: int) -> UUID:
        return uuid5(self.run_id, f"{tick}:{self._ordinal_by_tick[tick]}")

    def post_transaction(
        self,
        legs: Sequence[Leg],
        *,
        tick: int,
        cause: Event,
        allow_negative: frozenset[str] = frozenset(),
    ) -> UUID:
        return self._post_transaction(
            legs,
            tick=tick,
            cause=cause,
            allow_negative=allow_negative,
            allow_issuance=False,
        )

    def _post_transaction(
        self,
        legs: Sequence[Leg],
        *,
        tick: int,
        cause: Event,
        allow_negative: frozenset[str],
        allow_issuance: bool,
    ) -> UUID:
        if len(legs) < 2:
            raise LedgerError("P1: a transaction requires at least two legs")
        if cause.run_id != self.run_id or cause.tick != tick:
            raise LedgerError("P7: cause must belong to this run and tick")
        seen: set[tuple[str, int, str]] = set()
        deltas: dict[str, int] = defaultdict(int)
        for leg in legs:
            if leg.direction not in {-1, 1}:
                raise LedgerError("P2: direction must be +1 or -1")
            if leg.amount_cents <= 0:
                raise LedgerError("P2: amount_cents must be positive")
            if leg.reason not in REASONS:
                raise LedgerError(f"unknown ledger reason: {leg.reason}")
            key = (leg.account_id, leg.direction, leg.reason)
            if key in seen:
                raise LedgerError("P5: duplicate canonical leg")
            seen.add(key)
            account = self._open_account(leg.account_id)
            if account.currency != "POL":
                raise LedgerError("P4: account currency must be POL")
            if account.code == "iss" and not allow_issuance:
                raise LedgerError("issuance can only be posted through issue_base_money")
            deltas[leg.account_id] += leg.direction * leg.amount_cents
        if sum(deltas.values()) != 0:
            raise LedgerError("P3: transaction is not balanced")
        self._validate_allow_negative(allow_negative)
        for value, delta in deltas.items():
            account = self._accounts[value]
            new_balance = account.balance_cents + delta
            if value not in allow_negative:
                polarity = POLARITY[account.code]
                if polarity == "asset" and new_balance < 0:
                    raise LedgerError(f"P6: asset account would be negative: {value}")
                if polarity == "liability" and new_balance > 0:
                    raise LedgerError(f"P6: liability account would be positive: {value}")

        ordinal = self._ordinal_by_tick[tick]
        txn_id = self.next_txn_id(tick)
        self._ordinal_by_tick[tick] = ordinal + 1
        entries = tuple(
            Entry(
                txn_id,
                tick,
                cause.seq,
                leg.account_id,
                leg.direction,
                leg.amount_cents,
                leg.reason,
            )
            for leg in legs
        )
        for value, delta in deltas.items():
            account = self._accounts[value]
            account.balance_cents += delta
            account.entry_balance_cents += delta
            self._dirty_accounts.add(value)
        self._entries.extend(entries)
        self._pending_entries.extend(entries)
        self._posted_by_tick[tick].append(txn_id)
        return txn_id

    def transfer(
        self,
        src: str,
        dst: str,
        amount_cents: int,
        reason: str,
    ) -> list[Leg]:
        if amount_cents <= 0:
            raise LedgerError("transfer amount must be positive")
        src_code, _src_owner, _src_bank, _src_ref = parse_account_id(src)
        dst_code, _dst_owner, _dst_bank, _dst_ref = parse_account_id(dst)
        if src_code not in {"cash", "dep", "esc"} or dst_code not in {"cash", "dep", "esc"}:
            raise LedgerError("transfers require cash, deposit, or escrow endpoints")
        source_bank = bank_of(src)
        destination_bank = bank_of(dst)
        if source_bank == destination_bank:
            return [Leg(src, -1, amount_cents, reason), Leg(dst, 1, amount_cents, reason)]
        if source_bank is None or destination_bank is None:
            raise LedgerError("cross-bank transfer requires deposit or escrow endpoints")
        return [
            Leg(src, -1, amount_cents, reason),
            Leg(account_id("dpl", source_bank), 1, amount_cents, reason),
            Leg(account_id("res", source_bank), -1, amount_cents, reason),
            Leg(account_id("res", destination_bank), 1, amount_cents, reason),
            Leg(account_id("dpl", destination_bank), -1, amount_cents, reason),
            Leg(dst, 1, amount_cents, reason),
        ]

    def issue_base_money(
        self,
        legs: Sequence[Leg],
        *,
        tick: int,
        cause: Event,
    ) -> UUID:
        issuance_id = account_id("iss", "bk_cb")
        issuance_legs = [leg for leg in legs if leg.account_id == issuance_id]
        if len(issuance_legs) != 1:
            raise LedgerError("base-money issuance requires exactly one issuance leg")
        return self._post_transaction(
            legs,
            tick=tick,
            cause=cause,
            allow_negative=frozenset(),
            allow_issuance=True,
        )

    def commit_tick(self, tick: int) -> tuple[Mapping[str, Any], ...]:
        txn_ids = set(self._posted_by_tick.get(tick, ()))
        rows = tuple(
            {
                **asdict(entry),
                "txn_id": str(entry.txn_id),
                "run_id": str(self.run_id),
            }
            for entry in self._pending_entries
            if entry.txn_id in txn_ids
        )
        return rows

    async def flush(self, tick: int) -> None:
        rows = self.commit_tick(tick)
        if self.repo is not None and (self._dirty_accounts or rows):
            await self.repo.flush(
                [
                    {
                        **asdict(self._accounts[value]),
                        "account_type": ACCOUNT_TYPE[self._accounts[value].code],
                        "run_id": str(self.run_id),
                    }
                    for value in sorted(self._dirty_accounts)
                ],
                rows,
            )
        posted = set(self._posted_by_tick.pop(tick, ()))
        self._pending_entries = [
            entry for entry in self._pending_entries if entry.txn_id not in posted
        ]
        self._dirty_accounts.clear()

    def global_balance_cents(self) -> int:
        return sum(account.balance_cents for account in self._accounts.values())

    def materialisation_imbalance_cents(self) -> int:
        return sum(
            abs(account.balance_cents - account.entry_balance_cents)
            for account in self._accounts.values()
        )

    def deposit_imbalances(self) -> dict[str, int]:
        banks = sorted(
            account.owner_id
            for account in self._accounts.values()
            if account.code == "dpl" and account.owner_id != "bk_cb"
        )
        return {
            bank_id: sum(
                account.balance_cents
                for account in self._accounts.values()
                if account.code in {"dep", "esc"} and account.bank_id == bank_id
            )
            + self.balance(account_id("dpl", bank_id))
            for bank_id in banks
        }

    def base_money_imbalance_cents(self) -> int:
        issuance_id = account_id("iss", "bk_cb")
        if issuance_id not in self._accounts:
            return 0
        return (
            sum(
                account.balance_cents
                for account in self._accounts.values()
                if account.code == "cash" or (account.code == "res" and account.owner_id != "bk_cb")
            )
            + sum(
                account.balance_cents
                for account in self._accounts.values()
                if account.code == "dep"
                and account.owner_id == "gv_treasury"
                and account.bank_id == "bk_cb"
            )
            + self.balance(issuance_id)
        )

    def dump(self) -> Mapping[str, Any]:
        return {
            "accounts": {
                value: asdict(account) for value, account in sorted(self._accounts.items())
            },
            "entries": [{**asdict(entry), "txn_id": str(entry.txn_id)} for entry in self._entries],
            "pending_entries": [
                {**asdict(entry), "txn_id": str(entry.txn_id)} for entry in self._pending_entries
            ],
            "dirty_accounts": sorted(self._dirty_accounts),
            "ordinal_by_tick": dict(sorted(self._ordinal_by_tick.items())),
            "posted_by_tick": {
                str(tick): [str(txn_id) for txn_id in txn_ids]
                for tick, txn_ids in sorted(self._posted_by_tick.items())
            },
        }

    def load(self, state: Mapping[str, Any]) -> None:
        raw_accounts = state.get("accounts")
        if not isinstance(raw_accounts, Mapping):
            raise LedgerError("checkpoint accounts must be a mapping")
        self._accounts = {
            str(value): Account(**dict(raw))
            for value, raw in sorted(raw_accounts.items())
            if isinstance(raw, Mapping)
        }
        self._entries = self._restore_entries(state.get("entries", ()))
        self._pending_entries = self._restore_entries(state.get("pending_entries", ()))
        self._dirty_accounts = {str(value) for value in state.get("dirty_accounts", ())}
        self._ordinal_by_tick = defaultdict(
            int,
            {int(tick): int(ordinal) for tick, ordinal in state.get("ordinal_by_tick", {}).items()},
        )
        self._posted_by_tick = defaultdict(
            list,
            {
                int(tick): [UUID(str(value)) for value in values]
                for tick, values in state.get("posted_by_tick", {}).items()
            },
        )

    def _restore_entries(self, rows: Any) -> list[Entry]:
        if not isinstance(rows, Sequence):
            raise LedgerError("checkpoint entries must be a sequence")
        restored: list[Entry] = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise LedgerError("checkpoint entry must be a mapping")
            data = dict(raw)
            data["txn_id"] = UUID(str(data["txn_id"]))
            restored.append(Entry(**data))
        return restored

    def _account(self, value: str) -> Account:
        try:
            return self._accounts[value]
        except KeyError as exc:
            raise LedgerError(f"unknown account: {value}") from exc

    def _open_account(self, value: str) -> Account:
        account = self._account(value)
        if account.closed_tick is not None:
            raise LedgerError(f"account is closed: {value}")
        return account

    @staticmethod
    def _validate_allow_negative(values: frozenset[str]) -> None:
        for value in values:
            code, owner, bank_id, _ref = parse_account_id(value)
            reserve_overdraft = code == "res"
            treasury_overdraft = code == "dep" and owner == "gv_treasury" and bank_id == "bk_cb"
            if not reserve_overdraft and not treasury_overdraft:
                raise LedgerError(f"unapproved allow_negative account: {value}")


class CommitmentLedger:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self._tick: int | None = None
        self._committed: dict[str, int] = {}

    def available(self, owner_id: str, tick: int) -> int:
        self.reset(tick)
        return self.ledger.liquid(owner_id) - self._committed.get(owner_id, 0)

    def commit(self, owner_id: str, cents: int, tick: int) -> bool:
        if cents < 0:
            raise LedgerError("commitment must be non-negative")
        if cents > self.available(owner_id, tick):
            return False
        self._committed[owner_id] = self._committed.get(owner_id, 0) + cents
        return True

    def reset(self, tick: int) -> None:
        if self._tick != tick:
            self._tick = tick
            self._committed.clear()

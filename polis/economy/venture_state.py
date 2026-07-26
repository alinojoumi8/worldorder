from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, cast


@dataclass(slots=True)
class StartupState:
    startup_id: str
    firm_id: str
    founder_id: str
    thesis: str
    sector: str
    founded_tick: int
    initial_capital_cents: int
    burn_rate_cents: int
    stage: str = "pre_seed"
    runway_ticks: int = 0
    revenue_ttm_cents: int = 0
    total_raised_cents: int = 0
    status: str = "active"


@dataclass(slots=True)
class VCFundState:
    fund_id: str
    firm_id: str
    gp_agent_id: str
    committed_cents: int
    called_cents: int
    deployed_cents: int
    vintage_tick: int
    thesis: str
    management_fee_bp: int
    carry_bp: int
    hurdle_bp: int
    lps: dict[str, int] = field(default_factory=dict)
    status: str = "investing"


@dataclass(slots=True)
class CapTableState:
    firm_id: str
    holder_id: str
    share_class: str
    shares: int
    invested_cents: int = 0
    round_id: str | None = None
    liq_pref_bp: int = 0
    participating: bool = False
    pro_rata: bool = False
    conversion_price_cents: int = 0


@dataclass(slots=True)
class FundingRoundState:
    round_id: str
    startup_id: str
    stage: str
    pre_money_cents: int
    amount_cents: int
    post_money_cents: int
    price_per_share_cents: int
    new_shares: int
    lead_investor_id: str
    participants: dict[str, int]
    option_pool_shares: int
    liq_pref_bp: int
    participating: bool
    closed_tick: int


@dataclass(slots=True)
class PitchState:
    pitch_id: str
    startup_id: str
    founder_id: str
    investor_id: str
    ask_cents: int
    pre_money_ask_cents: int
    deck_text: str
    made_tick: int
    status: str = "open"
    conviction_bp: int | None = None
    valuation_view_cents: int | None = None
    verdict: str | None = None


@dataclass(slots=True)
class TermSheetState:
    term_sheet_id: str
    startup_id: str
    investor_id: str
    pre_money_cents: int
    amount_cents: int
    security: str
    liq_pref_bp: int
    participating: bool
    pro_rata: bool
    board_seat: bool
    option_pool_bp: int
    anti_dilution: str
    issued_tick: int
    expires_tick: int
    status: str = "open"


@dataclass(slots=True)
class AcquisitionState:
    deal_id: str
    acquirer_id: str
    target_id: str
    offer_cents: int
    per_share_cents: int
    consideration: str
    stock_ratio_bp: int
    premium_bp: int
    integration_mode: str
    financing: str
    proposed_tick: int
    expires_tick: int
    accepting_holders: dict[str, int] = field(default_factory=dict)
    status: str = "proposed"


@dataclass(slots=True)
class ClaimState:
    claim_id: str
    case_id: str
    creditor_id: str
    claim_cents: int
    priority_class: int
    collateral_ref: str | None = None
    loan_id: str | None = None
    paid_cents: int = 0


@dataclass(slots=True)
class BankruptcyCaseState:
    case_id: str
    entity_id: str
    entity_type: str
    trigger: str
    assets_cents: int
    liabilities_cents: int
    filed_tick: int
    stay_until_tick: int
    status: str = "open"
    liquidation_tick: int | None = None
    estate_cents: int = 0
    resolved_tick: int | None = None


@dataclass(slots=True)
class VentureState:
    startups: dict[str, StartupState] = field(default_factory=dict)
    funds: dict[str, VCFundState] = field(default_factory=dict)
    cap_table: dict[str, CapTableState] = field(default_factory=dict)
    rounds: dict[str, FundingRoundState] = field(default_factory=dict)
    pitches: dict[str, PitchState] = field(default_factory=dict)
    term_sheets: dict[str, TermSheetState] = field(default_factory=dict)
    acquisitions: dict[str, AcquisitionState] = field(default_factory=dict)
    bankruptcies: dict[str, BankruptcyCaseState] = field(default_factory=dict)
    claims: dict[str, ClaimState] = field(default_factory=dict)
    insolvency_since_tick: dict[str, int] = field(default_factory=dict)
    credit_flags_until_tick: dict[str, int] = field(default_factory=dict)
    dividends_by_tick: dict[int, dict[str, int]] = field(default_factory=dict)
    fund_distributions_cents: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def cap_key(firm_id: str, holder_id: str, share_class: str) -> str:
        return f"{firm_id}:{holder_id}:{share_class}"

    def shares(self, firm_id: str) -> int:
        return sum(row.shares for row in self.cap_table.values() if row.firm_id == firm_id)

    def dump(self) -> Mapping[str, Any]:
        return {
            "startups": {key: asdict(row) for key, row in sorted(self.startups.items())},
            "funds": {key: asdict(row) for key, row in sorted(self.funds.items())},
            "cap_table": {key: asdict(row) for key, row in sorted(self.cap_table.items())},
            "rounds": {key: asdict(row) for key, row in sorted(self.rounds.items())},
            "pitches": {key: asdict(row) for key, row in sorted(self.pitches.items())},
            "term_sheets": {key: asdict(row) for key, row in sorted(self.term_sheets.items())},
            "acquisitions": {key: asdict(row) for key, row in sorted(self.acquisitions.items())},
            "bankruptcies": {key: asdict(row) for key, row in sorted(self.bankruptcies.items())},
            "claims": {key: asdict(row) for key, row in sorted(self.claims.items())},
            "insolvency_since_tick": dict(sorted(self.insolvency_since_tick.items())),
            "credit_flags_until_tick": dict(sorted(self.credit_flags_until_tick.items())),
            "dividends_by_tick": {
                str(tick): dict(sorted(rows.items()))
                for tick, rows in sorted(self.dividends_by_tick.items())
            },
            "fund_distributions_cents": dict(sorted(self.fund_distributions_cents.items())),
        }

    @classmethod
    def load(cls, raw: Mapping[str, Any]) -> VentureState:
        def rows(name: str) -> Mapping[object, object]:
            value = raw.get(name, {})
            if not isinstance(value, Mapping):
                raise ValueError(f"venture checkpoint {name} must be a mapping")
            return value

        def load_rows(name: str, row_type: type[Any]) -> dict[str, Any]:
            return {
                str(key): row_type(**dict(value))
                for key, value in sorted(rows(name).items())
                if isinstance(value, Mapping)
            }

        state = cls()
        state.startups = cast(dict[str, StartupState], load_rows("startups", StartupState))
        state.funds = cast(dict[str, VCFundState], load_rows("funds", VCFundState))
        state.cap_table = cast(
            dict[str, CapTableState],
            load_rows("cap_table", CapTableState),
        )
        state.rounds = cast(
            dict[str, FundingRoundState],
            load_rows("rounds", FundingRoundState),
        )
        state.pitches = cast(dict[str, PitchState], load_rows("pitches", PitchState))
        state.term_sheets = cast(
            dict[str, TermSheetState],
            load_rows("term_sheets", TermSheetState),
        )
        state.acquisitions = cast(
            dict[str, AcquisitionState],
            load_rows("acquisitions", AcquisitionState),
        )
        state.bankruptcies = cast(
            dict[str, BankruptcyCaseState],
            load_rows("bankruptcies", BankruptcyCaseState),
        )
        state.claims = cast(dict[str, ClaimState], load_rows("claims", ClaimState))
        state.insolvency_since_tick = {
            str(entity): int(cast(Any, tick))
            for entity, tick in rows("insolvency_since_tick").items()
        }
        state.credit_flags_until_tick = {
            str(entity): int(cast(Any, tick))
            for entity, tick in rows("credit_flags_until_tick").items()
        }
        state.dividends_by_tick = {
            int(cast(Any, tick)): {
                str(firm_id): int(cast(Any, cents))
                for firm_id, cents in cast(Mapping[object, object], values).items()
            }
            for tick, values in rows("dividends_by_tick").items()
            if isinstance(values, Mapping)
        }
        state.fund_distributions_cents = {
            str(fund_id): int(cast(Any, cents))
            for fund_id, cents in rows("fund_distributions_cents").items()
        }
        return state

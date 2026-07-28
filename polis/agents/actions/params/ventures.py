from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from polis.agents.actions.params.base import ActionParams, Cents


class FoundCompanyParams(ActionParams):
    name: str
    sector: str
    place_id: str
    initial_capital_cents: int = Field(ge=1)
    is_startup: bool = False
    is_fund: bool = False
    thesis: str = ""


class PitchParams(ActionParams):
    startup_id: str
    investor_id: str
    ask_cents: int = Field(ge=1)
    pre_money_ask_cents: int = Field(ge=1)
    deck_text: str


class IssueTermSheetParams(ActionParams):
    startup_id: str
    investor_id: str
    pre_money_cents: int = Field(ge=1)
    amount_cents: int = Field(ge=1)
    security: Literal["common", "preferred"] = "preferred"
    liq_pref_bp: int = Field(default=10_000, ge=0)
    participating: bool = False
    pro_rata: bool = True
    board_seat: bool = False
    option_pool_bp: int = Field(default=1_000, ge=0, lt=10_000)
    anti_dilution: Literal["none", "broad_weighted", "full_ratchet"] = "broad_weighted"


class InvestParams(ActionParams):
    target_id: str
    cents: int = Field(ge=1)
    instrument: Literal["round", "lp_commitment", "bond"] = "round"
    term_sheet_id: str | None = None


class AcquireParams(ActionParams):
    acquirer_id: str
    target_id: str
    offer_cents: int = Field(ge=1)
    consideration: Literal["cash", "stock", "mixed"] = "cash"
    stock_ratio_bp: int = Field(default=0, ge=0, le=10_000)
    integration_mode: Literal["absorb", "standalone", "asset_sale"] = "absorb"
    financing: str = "cash"


class SellStakeParams(ActionParams):
    firm_id: str
    qty: int = Field(ge=1)
    price_cents: int | None = Field(default=None, ge=1)
    deal_id: str | None = None


class FileBankruptcyParams(ActionParams):
    entity_id: str | None = None
    reason: str = "voluntary"


class DeclareDividendParams(ActionParams):
    firm_id: str
    total_cents: Cents | None = None
    per_share_cents: Cents | None = None

    @model_validator(mode="after")
    def exactly_one_dividend_basis(self) -> DeclareDividendParams:
        if (self.total_cents is None) == (self.per_share_cents is None):
            raise ValueError("exactly one of total_cents or per_share_cents is required")
        return self

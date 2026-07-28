from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from polis.agents.actions.params.base import ActionParams, PositiveCents


class SubmitOrderParams(ActionParams):
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"] = "limit"
    qty: int = Field(ge=1)
    limit_price_cents: PositiveCents | None = None
    flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def limit_orders_require_price(self) -> SubmitOrderParams:
        if self.order_type == "limit" and self.limit_price_cents is None:
            raise ValueError("limit_price_cents is required for limit orders")
        return self


class CancelOrderParams(ActionParams):
    order_id: str


class ShortParams(ActionParams):
    symbol: str
    qty: int = Field(ge=1)
    limit_price_cents: int = Field(ge=1)
    collateral_cents: int = Field(ge=1)


class IpoListParams(ActionParams):
    firm_id: str
    symbol: str
    shares_offered: int = Field(ge=1)
    primary_shares: int = Field(ge=0)
    secondary_shares: int = Field(ge=0)
    price_low_cents: int = Field(ge=1)
    price_high_cents: int = Field(ge=1)
    underwriter_bank_id: str

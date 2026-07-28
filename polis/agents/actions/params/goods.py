from __future__ import annotations

from pydantic import Field

from polis.agents.actions.params.base import ActionParams, Cents


class BuyGoodParams(ActionParams):
    sku: str
    qty: int = Field(ge=1, le=1_000)
    seller_firm_id: str
    max_unit_price_cents: Cents


class SetPriceParams(ActionParams):
    firm_id: str
    sku: str
    price_cents: int = Field(ge=1)


class ProduceParams(ActionParams):
    sku: str


class RestockParams(ActionParams):
    sku: str
    qty: int = Field(ge=1)

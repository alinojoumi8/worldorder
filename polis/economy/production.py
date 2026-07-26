from __future__ import annotations

from decimal import Decimal, localcontext

from polis.config.mechanisms import mechanism
from polis.economy.money import MONEY_CTX


@mechanism(
    "firms.production_cobb_douglas",
    entails=(
        "Output has constant returns jointly in labour and capital, diminishing returns "
        "to either input alone, and is multiplicatively separable in productivity."
    ),
    config_key="firms.beta_capital_bp",
)
def production_output_micro(
    *,
    productivity_bp: int,
    capital_cents: int,
    capital_ref_cents: int,
    effective_labour_bp: int,
    beta_capital_bp: int,
    yield_units: int,
) -> int:
    if (
        productivity_bp <= 0
        or capital_cents <= 0
        or capital_ref_cents <= 0
        or effective_labour_bp <= 0
        or yield_units <= 0
    ):
        return 0
    with localcontext(MONEY_CTX):
        beta = Decimal(beta_capital_bp) / Decimal(10_000)
        labour_share = Decimal(1) - beta
        capital_factor = (Decimal(capital_cents) / Decimal(capital_ref_cents)) ** beta
        labour_factor = (Decimal(effective_labour_bp) / Decimal(10_000)) ** labour_share
        output = (
            Decimal(productivity_bp)
            / Decimal(10_000)
            * capital_factor
            * labour_factor
            * Decimal(yield_units)
            * Decimal(1_000_000)
        )
    return int(output)

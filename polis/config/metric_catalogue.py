from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final, Literal

from polis.config.canon import canonical_bytes, sha256_hex

Cadence = Literal[
    "tick",
    "sim_day",
    "sim_week",
    "sim_month",
    "sim_quarter",
    "sim_year",
]


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    unit: str
    cadence: Cadence
    definition: str
    research_questions: tuple[str, ...] = ()

    @property
    def definition_hash(self) -> str:
        return sha256_hex(canonical_bytes(asdict(self)))


def _definition(
    metric_id: str,
    unit: str,
    definition: str,
    *,
    cadence: Cadence = "tick",
    research_questions: tuple[str, ...] = (),
) -> MetricDefinition:
    return MetricDefinition(metric_id, unit, cadence, definition, research_questions)


METRICS: Final[dict[str, MetricDefinition]] = {
    item.metric_id: item
    for item in (
        _definition("city.population", "agents", "Count of living citizens."),
        _definition(
            "city.wellbeing_mean",
            "index_0_100",
            "Arithmetic mean of living citizen wellbeing.",
        ),
        _definition(
            "sys.cognition.deliberate_share",
            "bp",
            "10,000 times deliberate routed citizens divided by awake citizens.",
            research_questions=("T8", "T9"),
        ),
        _definition(
            "sys.cognition.reflect_share",
            "bp",
            "10,000 times reflection routed citizens divided by awake citizens.",
        ),
        _definition(
            "sys.cognition.salience_cutoff",
            "dimensionless_float",
            "Minimum salience among deliberate-routed citizens.",
            research_questions=("T8",),
        ),
        _definition(
            "sys.cognition.salience_p50",
            "dimensionless_float",
            "Median salience score among awake citizens.",
            research_questions=("T8",),
        ),
        _definition(
            "sys.cognition.salience_p90",
            "dimensionless_float",
            "90th percentile salience score among awake citizens.",
            research_questions=("T8",),
        ),
        _definition(
            "sys.memory.count",
            "memories",
            "Count of retained memory records.",
        ),
        _definition(
            "sys.actions.entropy",
            "nats",
            "Shannon entropy over resolved M1 action types in the tick.",
        ),
        _definition(
            "sys.actions.unique",
            "action_types",
            "Count of distinct resolved M1 action types in the tick.",
        ),
        _definition(
            "education.mean_skill",
            "dimensionless_float",
            "Mean skill level over living citizens and the closed skill vocabulary.",
            cadence="sim_day",
        ),
        _definition(
            "world.occupied_places",
            "places",
            "Count of places with at least one citizen after movement resolution.",
        ),
        _definition(
            "gdp_nominal",
            "cents",
            "Quarter expenditure GDP: household consumption plus capital and government "
            "purchases plus the change in inventory valued at unit cost; transfers and "
            "asset trades are excluded.",
            cadence="sim_quarter",
            research_questions=("A1", "A4", "A6"),
        ),
        _definition(
            "gdp_production",
            "cents",
            "Quarter production GDP: seller revenue less non-capital intermediate purchases.",
            cadence="sim_quarter",
            research_questions=("A1",),
        ),
        _definition(
            "gdp_real",
            "cents",
            "10,000 times quarter nominal GDP divided by the fixed-basket CPI.",
            cadence="sim_quarter",
            research_questions=("A1", "A4"),
        ),
        _definition(
            "unemployment_rate",
            "bp",
            "10,000 times active job seekers without work divided by the labour force.",
            cadence="sim_day",
            research_questions=("A1", "A4", "B4"),
        ),
        _definition(
            "u_broad",
            "bp",
            "Broad unemployment rate; equal to measured unemployment until marginal "
            "attachment and involuntary part-time states are introduced.",
            cadence="sim_day",
            research_questions=("A1", "B4"),
        ),
        _definition(
            "lfpr",
            "bp",
            "10,000 times the labour force divided by living working-age citizens.",
            cadence="sim_day",
            research_questions=("A1", "A4"),
        ),
        _definition(
            "vacancy_rate",
            "bp",
            "10,000 times open vacancy headcount divided by vacancies plus employment.",
            cadence="sim_day",
            research_questions=("A1",),
        ),
        _definition(
            "cpi",
            "index_bp",
            "Fixed-genesis-basket Laspeyres consumer price index with base 10,000.",
            cadence="sim_day",
            research_questions=("A1", "A4"),
        ),
        _definition(
            "inflation_yoy",
            "bp",
            "10,000 times current CPI divided by CPI one simulation year earlier minus 10,000.",
            cadence="sim_quarter",
            research_questions=("A1", "A4"),
        ),
        _definition(
            "gini_wealth",
            "bp",
            "Gini coefficient in basis points over living adults' ledger net worth; "
            "omitted when aggregate net worth is non-positive.",
            cadence="sim_quarter",
            research_questions=("A2",),
        ),
        _definition(
            "share_negative_networth",
            "bp",
            "Share of living adults with negative ledger net worth.",
            cadence="sim_quarter",
            research_questions=("A2",),
        ),
        _definition(
            "gini_income",
            "bp",
            "Gini coefficient in basis points over trailing-year gross wages and transfers.",
            cadence="sim_quarter",
            research_questions=("A2", "B4"),
        ),
        _definition(
            "median_wage",
            "cents",
            "Median annual wage offer over open employments.",
            cadence="sim_week",
            research_questions=("A1", "A2"),
        ),
        _definition(
            "mean_wage",
            "cents",
            "Arithmetic mean annual wage offer over open employments.",
            cadence="sim_week",
            research_questions=("A2",),
        ),
        _definition(
            "wealth_share.top1",
            "bp",
            "Top one percent share of aggregate living-adult net worth.",
            cadence="sim_quarter",
            research_questions=("A2",),
        ),
        _definition(
            "wealth_share.top10",
            "bp",
            "Top ten percent share of aggregate living-adult net worth.",
            cadence="sim_quarter",
            research_questions=("A2",),
        ),
        _definition(
            "wealth_share.bottom50",
            "bp",
            "Bottom fifty percent share of aggregate living-adult net worth.",
            cadence="sim_quarter",
            research_questions=("A2",),
        ),
        _definition(
            "wealth_share_undefined",
            "count",
            "One when aggregate living-adult net worth is non-positive, otherwise zero.",
            cadence="sim_quarter",
            research_questions=("A2",),
        ),
        _definition(
            "labour_share",
            "bp",
            "10,000 times quarter gross wages paid divided by quarter nominal GDP.",
            cadence="sim_quarter",
            research_questions=("A1", "A2"),
        ),
        _definition(
            "hhi_sector",
            "index_bp",
            "Revenue-weighted mean of within-sector firm revenue Herfindahl indices.",
            cadence="sim_quarter",
            research_questions=("A6",),
        ),
        _definition(
            "market_index",
            "index_bp",
            "Divisor-adjusted capitalisation-weighted index of listed common equity, "
            "with the first valid observation based at 10,000.",
            cadence="sim_day",
            research_questions=("A3", "A6"),
        ),
        _definition(
            "price_fair_value_gap_bp",
            "bp",
            "Capitalisation-weighted listed-equity price divided by dividend-discount "
            "fair value, less one, in basis points; omitted when fair value is undefined.",
            cadence="sim_quarter",
            research_questions=("A3",),
        ),
        _definition(
            "venture_moic_bp",
            "bp",
            "10,000 times cumulative fund distributions divided by cumulative LP capital called.",
            cadence="sim_year",
            research_questions=("A6",),
        ),
        _definition(
            "inventory_value_cents",
            "cents",
            "Firm inventory quantity valued at its current integer unit cost; retained "
            "to make expenditure-GDP inventory changes auditable.",
            cadence="sim_quarter",
            research_questions=("A1",),
        ),
        _definition(
            "credit_outstanding_cents",
            "cents",
            "Outstanding principal over all non-written-off loans.",
            cadence="sim_week",
            research_questions=("A5",),
        ),
        _definition(
            "credit_growth_yoy",
            "bp",
            "Year-over-year percentage change in outstanding loan principal.",
            cadence="sim_week",
            research_questions=("A5",),
        ),
        _definition(
            "credit_to_gdp_bp",
            "bp",
            "10,000 times outstanding credit divided by trailing-year nominal GDP.",
            cadence="sim_quarter",
            research_questions=("A5",),
        ),
        _definition(
            "default_rate",
            "bp",
            "Loans entering default in the trailing week divided by loans current at "
            "the start of that window, annualised.",
            cadence="sim_week",
            research_questions=("A5",),
        ),
        _definition(
            "bank_capital_ratio",
            "bp",
            "System commercial-bank capital divided by risk-weighted assets.",
            cadence="sim_day",
            research_questions=("A5",),
        ),
        _definition("m0", "cents", "Base money: cash plus commercial-bank reserves."),
        _definition("m1", "cents", "Cash plus commercial-bank customer deposits."),
        _definition(
            "velocity",
            "bp",
            "10,000 times trailing-year nominal GDP divided by current M1.",
            cadence="sim_quarter",
            research_questions=("A4",),
        ),
        _definition(
            "policy_rate_bp",
            "bp",
            "Central-bank policy rate effective for the current tick.",
            cadence="sim_day",
            research_questions=("A4", "A5"),
        ),
        _definition(
            "lending_rate_bp",
            "bp",
            "Outstanding-principal-weighted mean annual rate on live loans.",
            cadence="sim_week",
            research_questions=("A4", "A5"),
        ),
        _definition(
            "term_spread_bp",
            "bp",
            "Outstanding-principal-weighted lending rate minus the policy rate.",
            cadence="sim_quarter",
            research_questions=("A4", "A5"),
        ),
    )
}

UNAVAILABLE_M1_METRICS: Final = frozenset(
    {
        "gdp_nominal",
        "gdp_production",
        "gdp_real",
        "unemployment_rate",
        "u_broad",
        "lfpr",
        "vacancy_rate",
        "cpi",
        "inflation_yoy",
        "gini_wealth",
        "gini_income",
        "share_negative_networth",
        "median_wage",
        "mean_wage",
        "wealth_share.top1",
        "wealth_share.top10",
        "wealth_share.bottom50",
        "wealth_share_undefined",
        "labour_share",
        "hhi_sector",
        "inventory_value_cents",
        "credit_outstanding_cents",
        "credit_growth_yoy",
        "credit_to_gdp_bp",
        "default_rate",
        "bank_capital_ratio",
        "m0",
        "m1",
        "velocity",
        "policy_rate_bp",
        "lending_rate_bp",
        "term_spread_bp",
        "market_index",
        "price_fair_value_gap_bp",
        "venture_moic_bp",
    }
)
M3_METRICS: Final = frozenset(
    {
        "market_index",
        "price_fair_value_gap_bp",
        "venture_moic_bp",
    }
)
FUTURE_METRICS: Final[frozenset[str]] = frozenset()
M2_METRICS: Final = UNAVAILABLE_M1_METRICS - M3_METRICS


def catalogue_manifest() -> dict[str, str]:
    return {
        metric_id: definition.definition_hash for metric_id, definition in sorted(METRICS.items())
    }

from __future__ import annotations

from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from typing import Any, Final, Literal

from polis.config.canon import canonical_bytes, sha256_hex
from polis.config.errors import PolisError

Unit = Literal[
    "cents",
    "bp",
    "index_bp",
    "count",
    "ratio_bp",
    "dimensionless_float",
    "usd",
    "tokens",
    "ticks",
    "ms",
    "sim_days",
    "sim_years",
]
Cadence = Literal[
    "tick",
    "sim_day",
    "sim_week",
    "sim_month",
    "sim_quarter",
    "sim_year",
    "on_event",
    "end_of_run",
]
MetricFamily = Literal[
    "economic",
    "social",
    "political",
    "legal",
    "demographic",
    "system",
]
ImplementationStatus = Literal["implemented", "declared"]


class MetricError(PolisError):
    """A metric catalogue entry violates the research contract."""


_FAMILY_GOVERNANCE: Final[dict[MetricFamily, str]] = {
    "economic": "06-ECONOMY-SPEC.md §12",
    "social": "07-SOCIETY-SPEC.md §10.1-§10.5",
    "political": "10-RESEARCH-AND-OBSERVABILITY.md §1.5",
    "legal": "07-SOCIETY-SPEC.md §10.7-§10.9",
    "demographic": "07-SOCIETY-SPEC.md §9-§10.4",
    "system": "10-RESEARCH-AND-OBSERVABILITY.md §1.8",
}
_FAMILY_CAVEATS: Final[dict[MetricFamily, str]] = {
    "economic": (
        "The value comes from a closed simulated ledger and configured population, "
        "not a sampled human national account."
    ),
    "social": (
        "Beliefs, ties, exposure, and communication are simulated constructs shaped "
        "by the declared prompts and mechanisms."
    ),
    "political": (
        "The simulated electorate and institutions use simplified eligibility, action-slot, "
        "and policy rules."
    ),
    "legal": (
        "Detection, charging, adjudication, and sanctions follow configured simulation "
        "mechanisms rather than a human legal system."
    ),
    "demographic": (
        "The population is accelerated, finite, and synthetic, so magnitudes are not "
        "directly comparable with human demographic estimates."
    ),
    "system": ("This is an engine diagnostic with no human-population interpretation."),
}


def _family_for(metric_id: str) -> MetricFamily:
    if metric_id.startswith(
        (
            "polarisation.",
            "exposure.",
            "consensus.",
            "trust.",
            "misinfo.",
            "network.",
            "segregation.",
        )
    ):
        return "social"
    if metric_id.startswith(("turnout", "politics.")):
        return "political"
    if metric_id.startswith(
        ("crime.", "conviction.", "charge.", "court.", "incarceration.", "prison.")
    ):
        return "legal"
    if metric_id.startswith(("demog.", "mobility.", "ige_")):
        return "demographic"
    if metric_id.startswith(("sys.", "gate.", "city.", "world.", "education.")):
        return "system"
    return "economic"


def _analogue_label(metric_id: str) -> str:
    return f"Human-statistics analogue: {metric_id.replace('.', ' ').replace('_', ' ')}"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    unit: Unit
    cadence: Cadence
    definition: str
    research_questions: tuple[str, ...]
    analogue: str
    analogue_caveat: str
    governed_by: str
    moved_by: tuple[int, ...] = ()
    movement_note: str = ""
    family: MetricFamily = "system"
    implementation_status: ImplementationStatus = "implemented"

    def __post_init__(self) -> None:
        if not self.metric_id.strip():
            raise MetricError("metric id must not be empty")
        if not self.definition.strip():
            raise MetricError(f"{self.metric_id}: definition must not be empty")
        if not self.research_questions:
            raise MetricError(f"{self.metric_id}: at least one research question is required")
        if not self.analogue.strip():
            raise MetricError(f"{self.metric_id}: analogue must not be empty")
        if not self.analogue_caveat.strip():
            raise MetricError(f"{self.metric_id}: analogue_caveat must not be empty")
        if not self.governed_by.strip():
            raise MetricError(f"{self.metric_id}: governed_by must not be empty")
        if not self.moved_by and not self.movement_note.strip():
            raise MetricError(
                f"{self.metric_id}: moved_by or an explicit movement_note is required"
            )

    @property
    def rq(self) -> tuple[str, ...]:
        return self.research_questions

    @property
    def definition_hash(self) -> str:
        return sha256_hex(
            canonical_bytes(
                {
                    "id": self.metric_id,
                    "definition": self.definition,
                    "unit": self.unit,
                    "cadence": self.cadence,
                }
            )
        )


def _definition(
    metric_id: str,
    unit: Unit,
    definition: str,
    *,
    cadence: Cadence = "tick",
    research_questions: tuple[str, ...] = ("SYS",),
    analogue: str | None = None,
    analogue_caveat: str | None = None,
    governed_by: str | None = None,
    moved_by: tuple[int, ...] = (),
    movement_note: str = "Derived from simulation state; no single event kind is authoritative.",
    family: MetricFamily | None = None,
    implementation_status: ImplementationStatus = "implemented",
) -> MetricDefinition:
    resolved_family = family or _family_for(metric_id)
    return MetricDefinition(
        metric_id=metric_id,
        unit=unit,
        cadence=cadence,
        definition=definition,
        research_questions=research_questions,
        analogue=analogue or _analogue_label(metric_id),
        analogue_caveat=analogue_caveat or _FAMILY_CAVEATS[resolved_family],
        governed_by=governed_by or _FAMILY_GOVERNANCE[resolved_family],
        moved_by=moved_by,
        movement_note=movement_note,
        family=resolved_family,
        implementation_status=implementation_status,
    )


METRICS: Final[dict[str, MetricDefinition]] = {
    item.metric_id: item
    for item in (
        _definition("city.population", "count", "Count of living citizens."),
        _definition(
            "city.wellbeing_mean",
            "dimensionless_float",
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
            "count",
            "Count of retained memory records.",
        ),
        _definition(
            "sys.actions.entropy",
            "dimensionless_float",
            "Shannon entropy over resolved M1 action types in the tick.",
        ),
        _definition(
            "sys.actions.unique",
            "count",
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
            "count",
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


def _declared(
    metric_id: str,
    unit: Unit,
    cadence: Cadence,
    research_questions: tuple[str, ...],
    definition: str,
    *,
    analogue: str | None = None,
    family: MetricFamily | None = None,
) -> MetricDefinition:
    return _definition(
        metric_id,
        unit,
        definition,
        cadence=cadence,
        research_questions=research_questions,
        analogue=analogue,
        family=family,
        implementation_status="declared",
    )


_DECLARED_METRICS: Final[tuple[MetricDefinition, ...]] = (
    # Economic catalogue entries that are not yet emitted by MetricCollector.
    _declared(
        "firm_entry_rate",
        "bp",
        "sim_year",
        ("A6",),
        "Annualised count of firms founded divided by firms live at the window start.",
    ),
    _declared(
        "firm_exit_rate",
        "bp",
        "sim_year",
        ("A6",),
        "Annualised count of firms dissolved divided by firms live at the window start.",
    ),
    _declared(
        "firm_size_tail_bp",
        "bp",
        "sim_year",
        ("A1",),
        "Hill tail exponent in basis points above the 80th percentile of live-firm headcount.",
    ),
    _declared(
        "bank.deposit_outflow_bp.*",
        "bp",
        "sim_day",
        ("A5", "B2"),
        "Net withdrawals over deposits during the day divided by opening deposit balance.",
    ),
    _declared(
        "ige_income_lifetime",
        "dimensionless_float",
        "end_of_run",
        ("A2", "B6"),
        "OLS slope of log child completed-lifetime income on log parent completed-lifetime income.",
    ),
    _declared(
        "wage_scar_bp",
        "bp",
        "sim_year",
        ("A1", "B4"),
        "Mean wage-change basis points after unemployment, grouped by completed spell length.",
    ),
    # Social catalogue.
    _declared(
        "polarisation.bc.*",
        "dimensionless_float",
        "sim_week",
        ("B1",),
        "Bimodality coefficient over living-adult stance values for one proposition.",
    ),
    _declared(
        "polarisation.dip.*",
        "dimensionless_float",
        "sim_week",
        ("B1",),
        "Hartigan dip statistic over living-adult stance values for one proposition.",
    ),
    _declared(
        "polarisation.dip_p.*",
        "dimensionless_float",
        "sim_week",
        ("B1",),
        "Seeded-bootstrap p-value for the proposition's Hartigan dip statistic.",
    ),
    _declared(
        "polarisation.var.*",
        "dimensionless_float",
        "sim_week",
        ("B1",),
        "Population variance of living-adult stance values for one proposition.",
    ),
    _declared(
        "polarisation.index",
        "dimensionless_float",
        "sim_week",
        ("B1",),
        "Arithmetic mean of proposition-level bimodality coefficients.",
    ),
    _declared(
        "polarisation.affective",
        "dimensionless_float",
        "sim_month",
        ("B1", "B3"),
        "Mean out-cluster live-tie valence minus mean in-cluster live-tie valence.",
    ),
    _declared(
        "exposure.crosscut",
        "bp",
        "sim_week",
        ("B1",),
        "Stance-weighted opposing-sign impressions divided by all impressions in seven days.",
    ),
    _declared(
        "exposure.crosscut_persuasive",
        "bp",
        "sim_week",
        ("B1",),
        "Cross-cutting impressions from sources with trust at least one half.",
    ),
    _declared(
        "exposure.crosscut_hostile",
        "bp",
        "sim_week",
        ("B1",),
        "Cross-cutting impressions from sources with trust below one half.",
    ),
    _declared(
        "consensus.time_to.*",
        "ticks",
        "on_event",
        ("B1", "B3"),
        "First tick when proposition stance variance stays below 0.02 for 30 sim-days.",
    ),
    _declared(
        "trust.generalised",
        "dimensionless_float",
        "sim_week",
        ("B1", "B2", "A5"),
        "Mean living-agent value of the generalised-trust proposition.",
    ),
    _declared(
        "trust.institution.*",
        "dimensionless_float",
        "sim_week",
        ("B1", "B2", "A5"),
        "Mean living-agent value of one institution-trust proposition.",
    ),
    _declared(
        "trust.dyadic",
        "dimensionless_float",
        "sim_week",
        ("B3",),
        "Mean trust value over live non-kin relationship edges.",
    ),
    _declared(
        "trust.behavioural",
        "bp",
        "sim_week",
        ("B3",),
        "Transactions with previously unrelated counterparties divided by all transactions.",
    ),
    _declared(
        "trust.promise_keeping",
        "bp",
        "sim_week",
        ("B3", "B5"),
        "Kept log-checkable obligations divided by kept plus broken obligations.",
    ),
    _declared(
        "trust.calibration",
        "dimensionless_float",
        "sim_month",
        ("B2",),
        "Correlation of agent outlet-trust values with accuracy of articles each agent saw.",
    ),
    _declared(
        "misinfo.exposure_reach.*",
        "count",
        "on_event",
        ("B2",),
        "Distinct agents whose feed or news slot contained the referenced false item.",
    ),
    _declared(
        "misinfo.adoption_reach.*",
        "count",
        "on_event",
        ("B2",),
        "Distinct agents moved at least 0.05 toward the referenced false claim.",
    ),
    _declared(
        "misinfo.believers.*",
        "count",
        "sim_day",
        ("B2",),
        "Agents at least 0.2 on the false side of the referenced item's truth value.",
    ),
    _declared(
        "misinfo.half_life.*",
        "sim_days",
        "on_event",
        ("B2",),
        "Days from peak believers until the believer count first falls by one half.",
    ),
    _declared(
        "misinfo.correction_efficacy.*",
        "dimensionless_float",
        "on_event",
        ("B2",),
        "Belief movement after a correction divided by pre-correction distance from truth.",
    ),
    _declared(
        "misinfo.share_of_impressions",
        "bp",
        "sim_week",
        ("B2",),
        "Impressions of false items divided by all feed and news impressions.",
    ),
    _declared(
        "misinfo.organic_share",
        "bp",
        "sim_week",
        ("B2",),
        "False items without a 99xxx cause ancestor divided by all false items.",
    ),
    _declared(
        "network.degree_mean",
        "count",
        "sim_week",
        ("B1", "B3"),
        "Arithmetic mean degree over the live non-kin relationship graph.",
    ),
    _declared(
        "network.degree_gini",
        "bp",
        "sim_week",
        ("B1", "B3"),
        "Gini coefficient in basis points over live non-kin graph degree.",
    ),
    _declared(
        "network.clustering",
        "dimensionless_float",
        "sim_week",
        ("B3",),
        "Global transitivity over the live non-kin relationship graph.",
    ),
    _declared(
        "network.assortativity.*",
        "dimensionless_float",
        "sim_week",
        ("B1", "B3"),
        "Newman assortativity coefficient for one declared agent attribute.",
    ),
    _declared(
        "network.modularity",
        "dimensionless_float",
        "sim_week",
        ("B1", "B3"),
        "Modularity of deterministic communities in the live non-kin graph.",
    ),
    _declared(
        "network.ei_index",
        "dimensionless_float",
        "sim_week",
        ("B1", "B3"),
        "External-minus-internal relationship edges divided by their sum.",
    ),
    _declared(
        "network.crosscut_tie_share",
        "bp",
        "sim_week",
        ("B1", "B3"),
        "Live ties crossing deterministic belief communities divided by all live ties.",
    ),
    _declared(
        "network.largest_component_share",
        "bp",
        "sim_week",
        ("B1", "B3"),
        "Agents in the largest connected component divided by agents in the graph.",
    ),
    _declared(
        "segregation.dissimilarity.*",
        "bp",
        "sim_month",
        ("A2", "B1"),
        "Duncan dissimilarity index over districts for one declared binary grouping.",
    ),
    _declared(
        "segregation.isolation.*",
        "bp",
        "sim_month",
        ("A2",),
        "District-weighted within-group exposure for one declared binary grouping.",
    ),
    _declared(
        "segregation.theil_h",
        "dimensionless_float",
        "sim_month",
        ("A2",),
        "Multigroup entropy segregation index over districts and wealth quintiles.",
    ),
    # Political catalogue.
    _declared(
        "turnout",
        "bp",
        "on_event",
        ("B1", "B4"),
        "Valid votes cast divided by agents eligible at the election tick.",
    ),
    _declared(
        "turnout.deliberate",
        "bp",
        "on_event",
        ("T8",),
        "Deliberate-routed voters divided by deliberate-routed eligible agents.",
    ),
    _declared(
        "turnout.by_quintile",
        "bp",
        "on_event",
        ("A2", "B4"),
        "Valid votes divided by eligible agents within each wealth quintile.",
    ),
    _declared(
        "turnout.differential",
        "bp",
        "on_event",
        ("A2", "B4"),
        "Highest minus lowest wealth-quintile turnout in basis points.",
    ),
    _declared(
        "politics.vote_share.*",
        "bp",
        "on_event",
        ("B1", "B4"),
        "Votes for one party's candidacies divided by valid votes in the election.",
    ),
    _declared(
        "politics.enp",
        "dimensionless_float",
        "on_event",
        ("B1",),
        "Inverse sum of squared party vote shares for one election.",
    ),
    _declared(
        "politics.policy_volatility",
        "count",
        "sim_year",
        ("A4", "B4"),
        "Count of policy-enactment events during one simulation year.",
    ),
    _declared(
        "politics.policy_delta_mean",
        "dimensionless_float",
        "sim_year",
        ("A4", "B4"),
        "Mean absolute policy change divided by each parameter's admissible range.",
    ),
    _declared(
        "politics.policy_reversal_rate",
        "bp",
        "sim_year",
        ("B1",),
        "Enactments moving toward the value two enactments prior divided by enactments.",
    ),
    _declared(
        "politics.incumbency_retention",
        "bp",
        "sim_year",
        ("B4",),
        "Elections retained by a running incumbent divided by elections with one.",
    ),
    _declared(
        "politics.platform_responsiveness",
        "dimensionless_float",
        "sim_year",
        ("B1", "B4"),
        "Correlation of proposition median-stance changes with lagged enacted-position changes.",
    ),
    # Legal catalogue.
    _declared(
        "crime.committed_rate",
        "dimensionless_float",
        "sim_month",
        ("B5",),
        "Committed-crime events divided by living-adult simulation-years in the window.",
    ),
    _declared(
        "crime.by_type.*",
        "dimensionless_float",
        "sim_month",
        ("B5",),
        "Committed events of one crime type divided by living-adult simulation-years.",
    ),
    _declared(
        "crime.reported_rate",
        "dimensionless_float",
        "sim_month",
        ("B5",),
        "Reported-crime events divided by living-adult simulation-years in the window.",
    ),
    _declared(
        "crime.detected_rate",
        "dimensionless_float",
        "sim_month",
        ("B5",),
        "Detected-crime events divided by living-adult simulation-years in the window.",
    ),
    _declared(
        "crime.dark_figure",
        "dimensionless_float",
        "sim_month",
        ("B5",),
        "Committed-crime count divided by reported-crime count.",
    ),
    _declared(
        "crime.mean_p_detect",
        "bp",
        "sim_month",
        ("B5",),
        "Mean realised detection probability over crimes live in the window.",
    ),
    _declared(
        "crime.victimisation",
        "bp",
        "sim_month",
        ("B5",),
        "Distinct crime victims divided by living adults.",
    ),
    _declared(
        "crime.recidivism",
        "bp",
        "sim_year",
        ("B5",),
        "Sanctioned agents committing again within one year divided by sanctioned agents.",
    ),
    _declared(
        "conviction.rate",
        "bp",
        "sim_month",
        ("B5",),
        "Guilty or liable judgments divided by all rendered judgments.",
    ),
    _declared(
        "conviction.per_crime",
        "bp",
        "sim_month",
        ("B5",),
        "Guilty criminal judgments divided by crimes committed.",
    ),
    _declared(
        "charge.rate",
        "bp",
        "sim_month",
        ("B5",),
        "Filed charges divided by detected crimes.",
    ),
    _declared(
        "court.backlog",
        "count",
        "sim_month",
        ("B5",),
        "Open court cases at the cadence tick.",
    ),
    _declared(
        "court.time_to_verdict",
        "ticks",
        "sim_month",
        ("B5",),
        "Median ticks from case filing to judgment for cases closed in the window.",
    ),
    _declared(
        "court.counsel_gap",
        "bp",
        "sim_month",
        ("B5",),
        "Counselled-case favorable-verdict rate minus uncounselled-case rate.",
    ),
    _declared(
        "court.bench_share",
        "bp",
        "sim_month",
        ("B5",),
        "Cases resolved by bench-rule judges divided by resolved cases.",
    ),
    _declared(
        "incarceration.rate",
        "bp",
        "sim_month",
        ("B5", "A2"),
        "Incarcerated living adults divided by living adults.",
    ),
    _declared(
        "incarceration.admissions",
        "count",
        "sim_month",
        ("B5", "A2"),
        "Incarceration admissions during the window.",
    ),
    _declared(
        "incarceration.mean_days",
        "sim_days",
        "sim_month",
        ("B5", "A2"),
        "Mean completed incarceration duration in simulation days.",
    ),
    _declared(
        "incarceration.by_quintile",
        "bp",
        "sim_month",
        ("B5", "A2"),
        "Incarcerated share within each wealth quintile.",
    ),
    _declared(
        "prison.utilisation",
        "bp",
        "sim_month",
        ("B5", "A2"),
        "Occupied incarceration capacity divided by configured capacity.",
    ),
    # Demographic catalogue.
    _declared(
        "demog.population",
        "count",
        "sim_day",
        ("SYS",),
        "Count of agents whose death tick is null.",
    ),
    _declared(
        "demog.birth_rate",
        "dimensionless_float",
        "sim_year",
        ("A2", "B6"),
        "Births divided by mean living population and window simulation-years, times 1,000.",
    ),
    _declared(
        "demog.death_rate",
        "dimensionless_float",
        "sim_year",
        ("A2",),
        "Deaths divided by mean living population and window simulation-years, times 1,000.",
    ),
    _declared(
        "demog.tfr",
        "dimensionless_float",
        "sim_year",
        ("SYS",),
        "Sum of five-year-band age-specific birth rates times band width.",
    ),
    _declared(
        "demog.life_expectancy_e0",
        "sim_years",
        "sim_year",
        ("A2",),
        "Period synthetic-cohort life expectancy from age-specific death rates.",
    ),
    _declared(
        "demog.life_expectancy_gap_q1q5",
        "sim_years",
        "sim_year",
        ("A2",),
        "Bottom-quintile period life expectancy minus top-quintile life expectancy at age 30.",
    ),
    _declared(
        "demog.median_age",
        "sim_years",
        "sim_month",
        ("SYS",),
        "Median age in simulation years over living agents.",
    ),
    _declared(
        "demog.dependency_ratio",
        "dimensionless_float",
        "sim_month",
        ("SYS",),
        "Non-working-age living agents divided by working-age living agents.",
    ),
    _declared(
        "demog.mean_household_size",
        "dimensionless_float",
        "sim_month",
        ("SYS",),
        "Living household members divided by live households.",
    ),
    _declared(
        "demog.net_migration_rate",
        "bp",
        "sim_year",
        ("A2",),
        "Net in-migrations divided by mean living population, annualised.",
    ),
    _declared(
        "ige_wealth_age40",
        "dimensionless_float",
        "end_of_run",
        ("A2",),
        "OLS slope of log child wealth at age 40 on log parent wealth at age 40.",
    ),
    _declared(
        "mobility.rank_rank",
        "dimensionless_float",
        "end_of_run",
        ("A2",),
        "Slope of child wealth percentile on parent wealth percentile.",
    ),
    _declared(
        "mobility.transition",
        "dimensionless_float",
        "end_of_run",
        ("A2",),
        "Parent-to-child wealth-quintile transition matrix encoded as deterministic cells.",
    ),
    _declared(
        "mobility.upward_q1",
        "bp",
        "end_of_run",
        ("A2",),
        "Children born in parent quintile one ending above quintile one.",
    ),
    _declared(
        "mobility.belief_ige",
        "dimensionless_float",
        "end_of_run",
        ("B6",),
        "Slope of child age-30 policy-stance vector on the parent stance vector.",
    ),
    # System catalogue additions.
    _declared(
        "sys.llm.calls",
        "count",
        "tick",
        ("SYS",),
        "Count of persisted model calls for the tick.",
    ),
    _declared(
        "sys.llm.tokens_in",
        "tokens",
        "tick",
        ("SYS",),
        "Input tokens over persisted model calls for the tick.",
    ),
    _declared(
        "sys.llm.tokens_out",
        "tokens",
        "tick",
        ("SYS",),
        "Output tokens over persisted model calls for the tick.",
    ),
    _declared(
        "sys.llm.cost_usd",
        "usd",
        "tick",
        ("SYS",),
        "Provider cost in USD over persisted model calls for the tick.",
    ),
    _declared(
        "sys.llm.cost_usd_cum",
        "usd",
        "tick",
        ("SYS",),
        "Cumulative provider cost in USD through the tick.",
    ),
    _declared(
        "sys.llm.cache_hit_rate",
        "bp",
        "tick",
        ("SYS",),
        "Cache-hit model calls divided by model calls for the tick.",
    ),
    _declared(
        "sys.llm.parse_failure_rate",
        "bp",
        "sim_day",
        ("SYS",),
        "Model calls with parsed_ok false divided by model calls.",
    ),
    _declared(
        "sys.llm.repair_rate",
        "bp",
        "sim_day",
        ("SYS",),
        "Model calls with repair attempts divided by model calls.",
    ),
    _declared(
        "sys.llm.latency_p50_ms",
        "ms",
        "sim_day",
        ("SYS",),
        "Median provider latency milliseconds over non-cache-hit calls.",
    ),
    _declared(
        "sys.llm.latency_p99_ms",
        "ms",
        "sim_day",
        ("SYS",),
        "99th-percentile provider latency milliseconds over non-cache-hit calls.",
    ),
    _declared(
        "sys.cognition.reflex_share",
        "bp",
        "tick",
        ("SYS",),
        "Reflex-routed awake agents divided by awake agents.",
    ),
    _declared(
        "sys.cognition.budget_exhausted",
        "count",
        "tick",
        ("SYS",),
        "One when a budget-exhausted event fired in the tick, otherwise zero.",
    ),
    _declared(
        "sys.cognition.force_routed",
        "count",
        "tick",
        ("SYS",),
        "Agents force-routed by mandatory obligations in the tick.",
    ),
    _declared(
        "sys.simawareness.rate",
        "bp",
        "sim_day",
        ("SYS",),
        "Simulation-aware flagged model calls divided by model calls.",
    ),
    _declared(
        "sys.action.entropy_norm",
        "dimensionless_float",
        "sim_day",
        ("SYS",),
        "Action-type entropy divided by the maximum entropy at observed support size.",
    ),
    _declared(
        "sys.action.js_divergence_mean",
        "dimensionless_float",
        "sim_week",
        ("SYS",),
        "Mean pairwise Jensen-Shannon divergence of agent action distributions.",
    ),
    _declared(
        "sys.text.distinct3",
        "bp",
        "sim_day",
        ("SYS",),
        "Distinct trigrams divided by all trigrams in speech and posts.",
    ),
    _declared(
        "sys.text.embed_cos_mean",
        "dimensionless_float",
        "sim_day",
        ("SYS",),
        "Mean pairwise cosine similarity over the seeded post-embedding sample.",
    ),
    _declared(
        "sys.action.reject_rate.*",
        "bp",
        "sim_day",
        ("SYS",),
        "Rejected submitted actions of one reason divided by submitted actions.",
    ),
    _declared(
        "sys.engine.tick_wall_ms_p50",
        "ms",
        "tick",
        ("SYS",),
        "Median tick wall-clock milliseconds from run metadata.",
    ),
    _declared(
        "sys.engine.tick_wall_ms_p99",
        "ms",
        "tick",
        ("SYS",),
        "99th-percentile tick wall-clock milliseconds from run metadata.",
    ),
    _declared(
        "sys.engine.phase_ms.*",
        "ms",
        "tick",
        ("SYS",),
        "Wall-clock milliseconds for one engine phase from run metadata.",
    ),
    _declared(
        "sys.engine.events_per_tick",
        "count",
        "tick",
        ("SYS",),
        "Persisted event count for the tick.",
    ),
    _declared(
        "sys.store.commit_ms",
        "ms",
        "tick",
        ("SYS",),
        "Commit wall-clock milliseconds from run metadata.",
    ),
    _declared(
        "sys.external.deadline_miss_rate",
        "bp",
        "sim_day",
        ("SYS",),
        "Missed external deadlines divided by externally driven ticks.",
    ),
    _declared(
        "sys.external.actions",
        "count",
        "sim_day",
        ("SYS",),
        "Accepted external actions during the simulation day.",
    ),
    _declared(
        "sys.ephemeral.dropped",
        "count",
        "tick",
        ("SYS",),
        "Ephemeral frames dropped under Redis backpressure.",
    ),
    _declared(
        "sys.invariant.*.violations",
        "count",
        "tick",
        ("SYS",),
        "Cumulative violations for one invariant id.",
    ),
    _declared(
        "gate.*.pass",
        "count",
        "on_event",
        ("SYS",),
        "One for a passing evaluated gate and zero for a failing gate.",
    ),
)

for _item in _DECLARED_METRICS:
    if _item.metric_id in METRICS:
        raise MetricError(f"duplicate metric id: {_item.metric_id}")
    METRICS[_item.metric_id] = _item

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
FUTURE_METRICS: Final[frozenset[str]] = frozenset(
    metric_id
    for metric_id, definition in METRICS.items()
    if definition.implementation_status == "declared"
)
M2_METRICS: Final = UNAVAILABLE_M1_METRICS - M3_METRICS


def spec(metric_id: str) -> MetricDefinition:
    """Return the exact or single wildcard definition for a concrete metric id."""

    exact = METRICS.get(metric_id)
    if exact is not None:
        return exact
    matches = [
        definition
        for pattern, definition in METRICS.items()
        if "*" in pattern and fnmatchcase(metric_id, pattern)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise MetricError(f"unknown metric id: {metric_id}")
    patterns = ", ".join(sorted(item.metric_id for item in matches))
    raise MetricError(f"ambiguous metric id {metric_id!r}; matched: {patterns}")


def catalogue_payload() -> list[dict[str, Any]]:
    """Return the deterministic, JSON-serializable catalogue."""

    payload: list[dict[str, Any]] = []
    for definition in sorted(METRICS.values(), key=lambda item: item.metric_id):
        row = asdict(definition)
        row["research_questions"] = list(definition.research_questions)
        row["moved_by"] = list(definition.moved_by)
        row["definition_hash"] = definition.definition_hash
        payload.append(row)
    return payload


def _markdown_cell(value: object) -> str:
    if isinstance(value, (tuple, list)):
        rendered = ", ".join(str(item) for item in value)
    else:
        rendered = str(value)
    return rendered.replace("|", r"\|").replace("\n", " ")


def catalogue_markdown() -> str:
    """Render the canonical catalogue as stable, reviewable Markdown."""

    headings: tuple[tuple[MetricFamily, str], ...] = (
        ("economic", "Economic metrics"),
        ("social", "Social metrics"),
        ("political", "Political metrics"),
        ("legal", "Legal metrics"),
        ("demographic", "Demographic metrics"),
        ("system", "System metrics"),
    )
    lines = [
        "# POLIS metric catalogue",
        "",
        (
            "Generated from `polis.config.metric_catalogue`; declared metrics are part of "
            "the research contract but are not reported as available until implemented."
        ),
        "",
    ]
    columns = (
        "ID",
        "Definition",
        "Unit",
        "Cadence",
        "RQ",
        "Analogue",
        "Caveat",
        "Governed by",
        "Moved by",
        "Status",
    )
    for family, heading in headings:
        lines.extend(
            [
                f"## {heading}",
                "",
                "| " + " | ".join(columns) + " |",
                "|" + "|".join("---" for _ in columns) + "|",
            ]
        )
        definitions = sorted(
            (item for item in METRICS.values() if item.family == family),
            key=lambda item: item.metric_id,
        )
        for definition in definitions:
            movement: object = (
                definition.moved_by if definition.moved_by else definition.movement_note
            )
            values: tuple[object, ...] = (
                f"`{definition.metric_id}`",
                definition.definition,
                definition.unit,
                definition.cadence,
                definition.rq,
                definition.analogue,
                definition.analogue_caveat,
                definition.governed_by,
                movement,
                definition.implementation_status,
            )
            lines.append("| " + " | ".join(_markdown_cell(value) for value in values) + " |")
        lines.append("")
    return "\n".join(lines)


def catalogue_manifest() -> dict[str, str]:
    return {
        metric_id: definition.definition_hash for metric_id, definition in sorted(METRICS.items())
    }

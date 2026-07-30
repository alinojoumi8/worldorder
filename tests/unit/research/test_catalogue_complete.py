from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from polis.cli.app import app
from polis.config.metric_catalogue import (
    FUTURE_METRICS,
    METRICS,
    MetricError,
    catalogue_markdown,
    catalogue_payload,
    spec,
)

EXPECTED_C24B_PATTERNS = {
    "firm_entry_rate",
    "firm_exit_rate",
    "firm_size_tail_bp",
    "bank.deposit_outflow_bp.*",
    "ige_income_lifetime",
    "wage_scar_bp",
    "polarisation.bc.*",
    "polarisation.dip.*",
    "polarisation.dip_p.*",
    "polarisation.var.*",
    "polarisation.index",
    "polarisation.affective",
    "exposure.crosscut",
    "exposure.crosscut_persuasive",
    "exposure.crosscut_hostile",
    "consensus.time_to.*",
    "trust.generalised",
    "trust.institution.*",
    "trust.dyadic",
    "trust.behavioural",
    "trust.promise_keeping",
    "trust.calibration",
    "misinfo.exposure_reach.*",
    "misinfo.adoption_reach.*",
    "misinfo.believers.*",
    "misinfo.half_life.*",
    "misinfo.correction_efficacy.*",
    "misinfo.share_of_impressions",
    "misinfo.organic_share",
    "network.degree_mean",
    "network.degree_gini",
    "network.clustering",
    "network.assortativity.*",
    "network.modularity",
    "network.ei_index",
    "network.crosscut_tie_share",
    "network.largest_component_share",
    "segregation.dissimilarity.*",
    "segregation.isolation.*",
    "segregation.theil_h",
    "turnout",
    "turnout.deliberate",
    "turnout.by_quintile",
    "turnout.differential",
    "politics.vote_share.*",
    "politics.enp",
    "politics.policy_volatility",
    "politics.policy_delta_mean",
    "politics.policy_reversal_rate",
    "politics.incumbency_retention",
    "politics.platform_responsiveness",
    "crime.committed_rate",
    "crime.by_type.*",
    "crime.reported_rate",
    "crime.detected_rate",
    "crime.dark_figure",
    "crime.mean_p_detect",
    "crime.victimisation",
    "crime.recidivism",
    "conviction.rate",
    "conviction.per_crime",
    "charge.rate",
    "court.backlog",
    "court.time_to_verdict",
    "court.counsel_gap",
    "court.bench_share",
    "incarceration.rate",
    "incarceration.admissions",
    "incarceration.mean_days",
    "incarceration.by_quintile",
    "prison.utilisation",
    "demog.population",
    "demog.birth_rate",
    "demog.death_rate",
    "demog.tfr",
    "demog.life_expectancy_e0",
    "demog.life_expectancy_gap_q1q5",
    "demog.median_age",
    "demog.dependency_ratio",
    "demog.mean_household_size",
    "demog.net_migration_rate",
    "ige_wealth_age40",
    "mobility.rank_rank",
    "mobility.transition",
    "mobility.upward_q1",
    "mobility.belief_ige",
    "sys.llm.calls",
    "sys.llm.tokens_in",
    "sys.llm.tokens_out",
    "sys.llm.cost_usd",
    "sys.llm.cost_usd_cum",
    "sys.llm.cache_hit_rate",
    "sys.llm.parse_failure_rate",
    "sys.llm.repair_rate",
    "sys.llm.latency_p50_ms",
    "sys.llm.latency_p99_ms",
    "sys.cognition.reflex_share",
    "sys.cognition.budget_exhausted",
    "sys.cognition.force_routed",
    "sys.simawareness.rate",
    "sys.action.entropy_norm",
    "sys.action.js_divergence_mean",
    "sys.text.distinct3",
    "sys.text.embed_cos_mean",
    "sys.action.reject_rate.*",
    "sys.engine.tick_wall_ms_p50",
    "sys.engine.tick_wall_ms_p99",
    "sys.engine.phase_ms.*",
    "sys.engine.events_per_tick",
    "sys.store.commit_ms",
    "sys.external.deadline_miss_rate",
    "sys.external.actions",
    "sys.ephemeral.dropped",
    "sys.invariant.*.violations",
    "gate.*.pass",
}


def test_complete_c24b_catalogue_has_required_contract_fields() -> None:
    assert METRICS.keys() >= EXPECTED_C24B_PATTERNS
    assert {
        metric_id
        for metric_id, definition in METRICS.items()
        if definition.implementation_status == "declared"
    } == FUTURE_METRICS
    assert {item.family for item in METRICS.values()} == {
        "economic",
        "social",
        "political",
        "legal",
        "demographic",
        "system",
    }
    for definition in METRICS.values():
        assert definition.research_questions
        assert definition.analogue
        assert definition.analogue_caveat
        assert definition.governed_by
        assert definition.moved_by or definition.movement_note
        assert len(definition.definition_hash) == 64
    assert METRICS["ige_income_lifetime"].family == "demographic"
    assert METRICS["crime.committed_rate"].unit == "dimensionless_float"
    assert METRICS["demog.birth_rate"].unit == "dimensionless_float"
    assert METRICS["demog.median_age"].unit == "sim_years"
    assert METRICS["sys.store.commit_ms"].unit == "ms"


def test_wildcard_lookup_resolves_concrete_ids_and_names_errors() -> None:
    assert spec("bank.deposit_outflow_bp.bk_02").metric_id == "bank.deposit_outflow_bp.*"
    assert spec("sys.invariant.INV-MONEY.violations").metric_id == "sys.invariant.*.violations"
    with pytest.raises(MetricError, match=r"unknown metric id: not\.registered"):
        spec("not.registered")


def test_catalogue_renderers_are_deterministic_and_cli_exposes_both_formats() -> None:
    markdown = catalogue_markdown()
    assert markdown == catalogue_markdown()
    assert markdown.startswith("# POLIS metric catalogue\n")
    assert markdown.count("\n## ") == 6
    assert "`unemployment_rate`" in markdown

    runner = CliRunner()
    markdown_result = runner.invoke(app, ["metrics", "catalogue", "--format", "md"])
    json_result = runner.invoke(app, ["metrics", "catalogue", "--format", "json"])

    assert markdown_result.exit_code == 0
    assert markdown_result.stdout.rstrip() == markdown.rstrip()
    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout) == catalogue_payload()


def test_catalogue_cli_rejects_unknown_format() -> None:
    result = CliRunner().invoke(
        app,
        ["metrics", "catalogue", "--format", "yaml"],
    )
    assert result.exit_code == 2
    assert "format must be md or json" in result.output

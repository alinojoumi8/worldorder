from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from polis.config.mechanisms import mechanism_manifest
from polis.config.settings import config_hash, load_settings
from polis.events.kinds import AGENT_BORN, INVARIANT_VIOLATED
from polis.events.types import Event
from scripts.calibrate_m3_stage0 import (
    PROTOCOL_ID,
    PROTOCOL_YEARS,
    _aggregate,
    _stage0_ineligibility_reasons,
)
from scripts.validate_m3_multiseed import DEFAULT_SEEDS, _GateEventSink


def test_stage0_config_is_a_non_fixture_mechanical_calibration() -> None:
    settings = load_settings(Path("configs/m3-calibration-stage0.yaml"))

    assert _stage0_ineligibility_reasons(settings) == []
    assert settings.population.initial_agents == 1_000
    assert settings.exchange.bootstrap_listing_day == 1
    assert settings.labour.min_match_score_bp == 6_500
    assert settings.ablations.reflex_only is True
    assert "exchange.zero_intelligence_trader" in mechanism_manifest(settings)
    assert "exchange.zero_intelligence_trader" not in mechanism_manifest(
        load_settings(Path("configs/m3-smoke.yaml"))
    )


def test_stage0_aggregate_keeps_failed_seed_in_denominator() -> None:
    reports = [
        {
            "protocol_id": PROTOCOL_ID,
            "seed": 1,
            "code_git_sha": "abc123",
            "base_config_hash": "mismatch-filled-below",
            "run_config_hash": "run-1",
            "terminal_hash": "terminal-1",
            "stage0_ineligibility_reasons": [],
            "gate_verdict": "pass",
            "ticks_per_second": 2.0,
            "gates": {"V2": {"verdict": "pass"}, "V3": {"verdict": "pass"}},
        },
        {
            "protocol_id": PROTOCOL_ID,
            "seed": 2,
            "code_git_sha": "abc123",
            "base_config_hash": "mismatch-filled-below",
            "run_config_hash": "run-2",
            "terminal_hash": "terminal-2",
            "stage0_ineligibility_reasons": [],
            "gate_verdict": "fail",
            "ticks_per_second": 1.0,
            "gates": {"V2": {"verdict": "pass"}, "V3": {"verdict": "fail"}},
        },
    ]
    configured = load_settings(Path("configs/m3-calibration-stage0.yaml"))
    for report in reports:
        report["base_config_hash"] = config_hash(configured)

    aggregate = _aggregate(
        reports,
        config=Path("configs/m3-calibration-stage0.yaml"),
        years=2,
    )

    assert aggregate["status"] == "mechanical_calibration_failed"
    assert aggregate["stage0_accepted"] is False
    assert aggregate["gate_matrix"]["2"]["V3"] == "fail"
    assert aggregate["provenance"]["consistent"] is True
    assert aggregate["provenance"]["seed_roster_complete"] is False
    assert aggregate["provenance"]["run_config_hashes"]["1"] == "run-1"


def test_stage0_rejects_mixed_code_provenance() -> None:
    configured = load_settings(Path("configs/m3-calibration-stage0.yaml"))
    base_hash = config_hash(configured)
    reports = [
        {
            "protocol_id": PROTOCOL_ID,
            "seed": seed,
            "code_git_sha": sha,
            "base_config_hash": base_hash,
            "run_config_hash": f"run-{seed}",
            "terminal_hash": f"terminal-{seed}",
            "stage0_ineligibility_reasons": [],
            "gate_verdict": "pass",
            "ticks_per_second": 2.0,
            "gates": {"V2": {"verdict": "pass"}, "V3": {"verdict": "pass"}},
        }
        for seed, sha in ((1, "abc123"), (2, "def456"))
    ]

    aggregate = _aggregate(
        reports,
        config=Path("configs/m3-calibration-stage0.yaml"),
        years=2,
    )

    assert aggregate["provenance"]["consistent"] is False
    assert aggregate["stage0_accepted"] is False


def test_stage0_acceptance_requires_the_frozen_seed_roster_and_duration() -> None:
    configured = load_settings(Path("configs/m3-calibration-stage0.yaml"))
    base_hash = config_hash(configured)
    reports = [
        {
            "protocol_id": PROTOCOL_ID,
            "seed": seed,
            "code_git_sha": "abc123",
            "base_config_hash": base_hash,
            "run_config_hash": f"run-{seed}",
            "terminal_hash": f"terminal-{seed}",
            "stage0_ineligibility_reasons": [],
            "gate_verdict": "pass",
            "ticks_per_second": 2.0,
            "gates": {"V2": {"verdict": "pass"}, "V3": {"verdict": "pass"}},
        }
        for seed in DEFAULT_SEEDS
    ]

    aggregate = _aggregate(
        reports,
        config=Path("configs/m3-calibration-stage0.yaml"),
        years=PROTOCOL_YEARS,
    )

    assert aggregate["provenance"]["seed_roster_complete"] is True
    assert aggregate["provenance"]["duration_complete"] is True
    assert aggregate["stage0_accepted"] is True


@pytest.mark.asyncio
async def test_gate_sink_discards_unneeded_events_without_changing_order() -> None:
    def event(seq: int, kind: int) -> Event:
        return Event(
            seq=seq,
            run_id=UUID(int=0),
            tick=1,
            sim_time=datetime(2100, 1, 1),
            kind=kind,
            actor_id=None,
            subject_ids=(),
            cause_seq=None,
            payload={},
            sig=None,
            prev_hash="0" * 64,
            hash=str(seq) * 64,
        )

    sink = _GateEventSink()
    await sink.append(
        (
            event(1, AGENT_BORN),
            event(2, INVARIANT_VIOLATED),
            event(3, AGENT_BORN),
        )
    )

    assert [row.seq for row in sink.events] == [2]

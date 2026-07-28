from __future__ import annotations

from dataclasses import fields, is_dataclass

from polis.agents.cognition.observation import (
    AgentBrief,
    MessageBrief,
    Observation,
    PlaceView,
    PostBrief,
    SelfView,
)


def test_perception_contract_contains_no_hidden_enforcement_parameters() -> None:
    forbidden = {
        "p_detect",
        "concealment",
        "evidence_strength",
        "crime_rate",
        "committed_crime_rate",
        "detected_crime_rate",
    }
    perception_types = (
        Observation,
        SelfView,
        PlaceView,
        AgentBrief,
        MessageBrief,
        PostBrief,
    )

    names = {
        field.name
        for perception_type in perception_types
        if is_dataclass(perception_type)
        for field in fields(perception_type)
    }
    assert forbidden.isdisjoint(names)
    assert "criminal_record" not in {field.name for field in fields(AgentBrief)}

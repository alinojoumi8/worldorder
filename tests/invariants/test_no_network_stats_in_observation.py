from dataclasses import fields, is_dataclass

from polis.agents.cognition import observation


def test_observation_views_never_expose_network_statistics_or_reach() -> None:
    banned = ("degree", "clustering", "centrality", "community", "reach")
    view_types = (
        observation.SelfView,
        observation.PlaceView,
        observation.AgentBrief,
        observation.MessageBrief,
        observation.PostBrief,
        observation.ArticleBrief,
        observation.Observation,
    )

    names = {
        field.name
        for view_type in view_types
        if is_dataclass(view_type)
        for field in fields(view_type)
    }

    assert not {
        name
        for name in names
        if any(fragment in name.lower() for fragment in banned)
    }

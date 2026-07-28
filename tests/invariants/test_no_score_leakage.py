import re
from dataclasses import fields
from pathlib import Path

from polis.agents.cognition.observation import Observation, PostBrief
from polis.society.media.news import NEWS_WRITE_SCHEMA, _safe_prompt_payload
from polis.society.protocols import ArticleBrief


def test_scores_are_absent_from_perception_and_prompt_contracts() -> None:
    forbidden = {"truthfulness", "accuracy"}
    for model in (Observation, PostBrief, ArticleBrief):
        assert forbidden.isdisjoint(field.name for field in fields(model))
    assert forbidden.isdisjoint(NEWS_WRITE_SCHEMA["properties"])
    assert _safe_prompt_payload(
        {
            "accuracy": 0.2,
            "nested": {"truthfulness": 0.1, "safe": "yes"},
        }
    ) == {"nested": {"safe": "yes"}}


def test_news_templates_use_narrative_line_and_include_memories() -> None:
    prompt_dir = Path("prompts/news_write")
    templates = [path.read_text(encoding="utf-8") for path in prompt_dir.glob("*.jinja")]
    assert len(templates) >= 4
    assert "reporter_memories" in (prompt_dir / "user.v1.jinja").read_text(encoding="utf-8")
    for template in templates:
        assert re.search(r"(slant|rigour)\s*[:=]\s*[-+]?\d", template, re.I) is None

from __future__ import annotations

from polis.agents.actions.params.base import ActionParams


class NullActionParams(ActionParams):
    replaced_type: str | None = None
    reason: str | None = None

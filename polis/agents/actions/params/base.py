from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Cents = Annotated[int, Field(ge=0)]
PositiveCents = Annotated[int, Field(ge=1)]
AgentId = Annotated[str, StringConstraints(pattern=r"^ag_[a-z0-9_]{1,32}$")]
FirmId = Annotated[str, StringConstraints(pattern=r"^fm_[a-z0-9_]{1,32}$")]
PlaceId = Annotated[str, StringConstraints(pattern=r"^pl_[a-z0-9_]{1,32}$")]
ShortText = Annotated[str, StringConstraints(max_length=1_000)]


class ActionParams(BaseModel):
    """Canonical, immutable parameter envelope shared by every action type."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

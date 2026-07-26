from __future__ import annotations

from datetime import UTC, datetime
from time import time_ns


def utc_now_naive() -> datetime:
    """Return wall time for operational metadata, never simulation state."""
    return datetime.fromtimestamp(time_ns() / 1_000_000_000, UTC).replace(tzinfo=None)

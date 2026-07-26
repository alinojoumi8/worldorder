from __future__ import annotations

import logging
from typing import cast
from uuid import UUID

import structlog


def configure_logging(
    level: str = "INFO", *, json_lines: bool = False, run_id: UUID | None = None
) -> None:
    logging.basicConfig(level=getattr(logging, level.upper()), format="%(message)s")
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if run_id is not None:
        structlog.contextvars.bind_contextvars(run_id=str(run_id))
    processors.append(
        structlog.processors.JSONRenderer()
        if json_lines
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(processors=processors)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))

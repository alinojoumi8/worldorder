from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID

import typer

from polis.config.mechanisms import mechanism_manifest
from polis.config.paths import repo_git_sha
from polis.config.settings import Settings, config_hash, load_settings

app = typer.Typer(no_args_is_help=True, help="POLIS deterministic city simulation")


def _parse_overrides(values: list[str]) -> dict[str, object]:
    import yaml

    root: dict[str, object] = {}
    for value in values:
        if "=" not in value:
            raise typer.BadParameter(f"override must be key=value: {value}")
        dotted, raw = value.split("=", 1)
        current = root
        parts = dotted.split(".")
        for part in parts[:-1]:
            child = current.setdefault(part, {})
            if not isinstance(child, dict):
                raise typer.BadParameter(f"override conflicts at {part}")
            current = child
        current[parts[-1]] = yaml.safe_load(raw)
    return root


@app.command()
def run(
    config: Annotated[Path, typer.Option(exists=True)] = Path("configs/baseline.yaml"),
    profile: Annotated[list[str] | None, typer.Option()] = None,
    set_: Annotated[list[str] | None, typer.Option("--set")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    memory_only: Annotated[bool, typer.Option("--memory-only")] = False,
) -> None:
    """Resolve and execute a deterministic POLIS run."""
    settings = load_settings(
        config,
        profiles=profile or (),
        overrides=_parse_overrides(set_ or []),
    )
    result = {
        "config_hash": config_hash(settings),
        "code_git_sha": repo_git_sha(),
        "active_mechanisms": mechanism_manifest(settings),
        "ticks": settings.run.ticks,
        "population": settings.population.initial_agents,
        "clock_profile": settings.clock.profile,
    }
    if dry_run:
        typer.echo(json.dumps(result, sort_keys=True) if json_output else yaml_like(result))
        return
    if memory_only:
        from polis.living_city import run_living_city

        simulation = asyncio.run(run_living_city(settings))
    else:
        from polis.store.living_city import run_persistent

        simulation = asyncio.run(run_persistent(settings))
    output = {**result, "report": asdict(simulation.report)}
    typer.echo(
        json.dumps(output, sort_keys=True, default=str) if json_output else yaml_like(output)
    )


def yaml_like(value: dict[str, object]) -> str:
    import yaml

    return cast(str, yaml.safe_dump(value, sort_keys=True)).strip()


async def _stored_settings(base: Settings, run_id: UUID) -> Settings:
    from polis.store.engine import Database
    from polis.store.operations import load_run_settings

    database = await Database.open(base.store, role="reader")
    try:
        return await load_run_settings(database, run_id)
    finally:
        await database.close()


@app.command()
def resume(
    run_id: UUID,
    config: Annotated[Path, typer.Option(exists=True)] = Path("configs/baseline.yaml"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Resume a stored run after verifying its deterministic replay prefix."""
    from polis.store.operations import resume_stored_run

    base = load_settings(config)
    settings = asyncio.run(_stored_settings(base, run_id))
    report = asyncio.run(resume_stored_run(settings, run_id))
    output = asdict(report)
    typer.echo(
        json.dumps(output, sort_keys=True, default=str) if json_output else yaml_like(output)
    )


@app.command()
def verify(
    run_id: UUID,
    config: Annotated[Path, typer.Option(exists=True)] = Path("configs/baseline.yaml"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Verify every stored event hash, sequence, tick and payload schema."""
    from polis.store.operations import verify_stored_run

    base = load_settings(config)
    settings = asyncio.run(_stored_settings(base, run_id))
    report = asyncio.run(verify_stored_run(settings, run_id))
    output = asdict(report)
    typer.echo(
        json.dumps(output, sort_keys=True, default=str) if json_output else yaml_like(output)
    )
    if not report.ok:
        raise typer.Exit(1)


@app.command()
def rebuild(
    run_id: UUID,
    config: Annotated[Path, typer.Option(exists=True)] = Path("configs/baseline.yaml"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Rebuild all M1 read models after an exact deterministic replay."""
    from polis.store.operations import rebuild_stored_run

    base = load_settings(config)
    settings = asyncio.run(_stored_settings(base, run_id))
    report = asyncio.run(rebuild_stored_run(settings, run_id))
    output = asdict(report)
    typer.echo(
        json.dumps(output, sort_keys=True, default=str) if json_output else yaml_like(output)
    )


@app.command()
def replay(
    run_id: UUID,
    config: Annotated[Path, typer.Option(exists=True)] = Path("configs/baseline.yaml"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Replay a stored run and compare its count and terminal chain hash."""
    from polis.store.operations import replay_stored_run

    base = load_settings(config)
    settings = asyncio.run(_stored_settings(base, run_id))
    report = asyncio.run(replay_stored_run(settings, run_id))
    output = asdict(report)
    typer.echo(
        json.dumps(output, sort_keys=True, default=str) if json_output else yaml_like(output)
    )
    if not report.exact:
        raise typer.Exit(1)


def _stub(owner: str) -> None:
    typer.echo(f"not implemented yet; owner: {owner}", err=True)
    raise typer.Exit(2)


@app.command()
def sweep() -> None:
    _stub("C24")


@app.command()
def gateway() -> None:
    _stub("C22")


@app.command()
def observe(
    config: Annotated[Path, typer.Option(exists=True)] = Path("configs/baseline.yaml"),
) -> None:
    """Serve the read-only Observatory API and built frontend."""
    import uvicorn

    from polis.observatory.api import create_app

    settings = load_settings(config)
    host, raw_port = settings.observatory.bind.rsplit(":", 1)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            host=host,
            port=int(raw_port),
            log_level="info",
        )
    )
    if sys.platform == "win32":
        import selectors

        def loop_factory() -> asyncio.AbstractEventLoop:
            return asyncio.SelectorEventLoop(selectors.SelectSelector())

        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(server.serve())
        return
    server.run()


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    app()

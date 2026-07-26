from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, cast

import typer

from polis.config.mechanisms import mechanism_manifest
from polis.config.paths import repo_git_sha
from polis.config.settings import config_hash, load_settings

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
) -> None:
    """Resolve and, once M0 is composed, execute a run."""
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
    typer.echo(json.dumps(result, sort_keys=True) if json_output else yaml_like(result))


def yaml_like(value: dict[str, object]) -> str:
    import yaml

    return cast(str, yaml.safe_dump(value, sort_keys=True)).strip()


def _stub(owner: str) -> None:
    typer.echo(f"not implemented yet; owner: {owner}", err=True)
    raise typer.Exit(2)


@app.command()
def resume() -> None:
    _stub("C04")


@app.command()
def verify() -> None:
    _stub("C02")


@app.command()
def rebuild() -> None:
    _stub("C03")


@app.command()
def replay() -> None:
    _stub("C24")


@app.command()
def sweep() -> None:
    _stub("C24")


@app.command()
def gateway() -> None:
    _stub("C22")


@app.command()
def observe() -> None:
    _stub("C23")


def main() -> None:
    app()

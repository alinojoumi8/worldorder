from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
PROMPTS_DIR: Final = REPO_ROOT / "prompts"
CONFIGS_DIR: Final = REPO_ROOT / "configs"
MIGRATIONS_DIR: Final = REPO_ROOT / "migrations"
PROFILES_DIR: Final = Path(__file__).with_name("profiles")


def repo_git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"

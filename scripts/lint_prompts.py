from __future__ import annotations

from pathlib import Path


def main() -> int:
    failures = [
        str(path)
        for path in sorted(Path("prompts").rglob("*.jinja"))
        if "version:" not in path.read_text(encoding="utf-8").splitlines()[0].lower()
    ]
    if failures:
        print("prompt templates missing a first-line version header:", *failures, sep="\n")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())

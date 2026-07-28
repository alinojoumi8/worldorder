from __future__ import annotations

import os
from pathlib import Path

import pytest

from polis.gateway.sdk.keys import Keypair


def test_keyfile_round_trip_is_private_and_refuses_overwrite(tmp_path: Path) -> None:
    key = Keypair.generate()
    target = tmp_path / "identity.json"

    assert key.save(target) is key
    loaded = Keypair.load(target)

    assert loaded is not None
    assert loaded.pubkey_hex == key.pubkey_hex
    assert loaded.agent_id == key.agent_id
    assert loaded.sign(b"message") == key.sign(b"message")
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        key.save(target)
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


def test_keyfile_save_retries_short_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_write = os.write

    def short_write(descriptor: int, data: bytes | memoryview) -> int:
        return real_write(descriptor, data[:3])

    monkeypatch.setattr(os, "write", short_write)
    key = Keypair.generate()
    target = tmp_path / "identity.json"

    key.save(target)

    loaded = Keypair.load(target)
    assert loaded is not None
    assert loaded.agent_id == key.agent_id


def test_keyfile_publish_failure_removes_the_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "identity.json"

    def fail_publish(source: object, destination: object) -> None:
        del source, destination
        raise OSError("publish failed")

    monkeypatch.setattr(os, "link", fail_publish)

    with pytest.raises(OSError, match="publish failed"):
        Keypair.generate().save(target)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_missing_keyfile_returns_none(tmp_path: Path) -> None:
    assert Keypair.load(tmp_path / "missing.json") is None


def test_tampered_keyfile_is_rejected(tmp_path: Path) -> None:
    first = Keypair.generate()
    second = Keypair.generate()
    target = tmp_path / "identity.json"
    first.save(target)
    text = target.read_text(encoding="utf-8").replace(first.pubkey_hex, second.pubkey_hex)
    target.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        Keypair.load(target)

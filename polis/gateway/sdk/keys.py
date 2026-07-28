"""Local ed25519 key custody for external-agent operators."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from polis.gateway.sdk.canonical import agent_id_for, sign


@dataclass(frozen=True, slots=True)
class Keypair:
    pubkey_hex: str
    agent_id: str
    _sk_bytes: bytes = field(repr=False)

    @classmethod
    def generate(cls) -> Keypair:
        private = Ed25519PrivateKey.generate()
        secret = private.private_bytes_raw()
        public = private.public_key().public_bytes_raw().hex()
        return cls(public, agent_id_for(public), secret)

    @classmethod
    def from_private_bytes(cls, secret: bytes) -> Keypair:
        if len(secret) != 32:
            raise ValueError("ed25519 private key must be exactly 32 bytes")
        private = Ed25519PrivateKey.from_private_bytes(secret)
        public = private.public_key().public_bytes_raw().hex()
        return cls(public, agent_id_for(public), bytes(secret))

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Keypair | None:
        source = Path(path)
        try:
            raw = source.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        payload: Any = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {
            "agent_id",
            "private_key_hex",
            "pubkey_hex",
            "version",
        }:
            raise ValueError("invalid POLIS key file")
        if payload["version"] != 1:
            raise ValueError("unsupported POLIS key file version")
        try:
            secret = bytes.fromhex(str(payload["private_key_hex"]))
        except ValueError as exc:
            raise ValueError("invalid private key encoding") from exc
        loaded = cls.from_private_bytes(secret)
        if payload["pubkey_hex"] != loaded.pubkey_hex or payload["agent_id"] != loaded.agent_id:
            raise ValueError("key file identity does not match its private key")
        return loaded

    def save(self, path: str | os.PathLike[str]) -> Keypair:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "agent_id": self.agent_id,
            "pubkey_hex": self.pubkey_hex,
            "private_key_hex": self._sk_bytes.hex(),
        }
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.chmod(temporary, 0o600)
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("key file write made no progress")
                remaining = remaining[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.link(temporary, target)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
        return self

    def sign(self, preimage: bytes) -> str:
        return sign(self._sk_bytes, preimage)

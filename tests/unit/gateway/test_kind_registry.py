from __future__ import annotations

from polis.events.kinds import KIND_REGISTRY, Persistence

EXPECTED = {
    20000,
    20001,
    20002,
    20003,
    20004,
    20005,
    20010,
    20011,
    20020,
    20021,
    20030,
    20031,
    20040,
    20041,
    20042,
    20050,
    20051,
    20060,
    20070,
    20090,
    20900,
}


def test_all_declared_gateway_kinds_have_owned_schemas() -> None:
    assert set(KIND_REGISTRY).intersection(range(20_000, 21_000)) == EXPECTED
    for kind in EXPECTED:
        spec = KIND_REGISTRY[kind]
        assert spec.owner.startswith("polis.gateway")
        assert spec.persistence is Persistence.PERSISTED
        assert spec.schema["type"] == "object"
        assert spec.schema["required"]

    status = KIND_REGISTRY[90_020]
    assert status.owner == "polis.gateway"
    assert status.persistence is Persistence.EPHEMERAL

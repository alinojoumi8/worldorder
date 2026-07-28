from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from psycopg.errors import ReadOnlySqlTransaction

from polis.config.settings import load_settings
from polis.observatory.api import create_app
from polis.simulation import run_id_for
from polis.store.engine import Database
from polis.store.living_city import run_persistent


@pytest.mark.integration
@pytest.mark.asyncio
async def test_observatory_is_read_only_fresh_and_marks_future_views_unavailable() -> None:
    settings = load_settings(
        Path("configs/smoke.yaml"),
        overrides={
            "run": {
                "name": "observatory-test",
                "seed": 8_100_003,
                "ticks": 3,
                "scale": 8,
            },
            "population": {"initial_agents": 8},
        },
    )
    run_id = run_id_for(settings)
    engine_db = await Database.open(settings.store, role="engine")
    await engine_db.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
    await engine_db.close()
    try:
        await run_persistent(settings)
        reader_db = await Database.open(settings.store, role="reader")
        try:
            with pytest.raises(ReadOnlySqlTransaction):
                await reader_db.fetch(
                    "DELETE FROM runs WHERE run_id=%s RETURNING run_id",
                    (run_id,),
                )
        finally:
            await reader_db.close()
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                health = await client.get("/api/v1/health")
                runs = await client.get("/api/v1/runs")
                detail = await client.get(f"/api/v1/runs/{run_id}")
                agents = await client.get(f"/api/v1/runs/{run_id}/agents")
                metric = await client.get(
                    f"/api/v1/runs/{run_id}/metrics",
                    params={"metric": "city.population"},
                )
                catalogue = await client.get(f"/api/v1/runs/{run_id}/metrics/catalogue")
                economy_metric = await client.get(
                    f"/api/v1/runs/{run_id}/metrics",
                    params={"metric": "unemployment_rate"},
                )
                inspector = await client.get(f"/api/v1/runs/{run_id}/agents/ag_0000/tick/1")
                mutation = await client.post("/api/v1/runs", json={})
                compare = await client.get("/api/v1/compare")

        assert health.status_code == 200
        assert health.json()["database"]["role"] == "reader"
        assert health.json()["database"]["alembic_head"] == "0013_society_core"
        assert runs.status_code == 200
        assert runs.json()["as_of_seq"] > 0
        assert "engine" in runs.json()
        assert detail.status_code == 200
        assert detail.json()["as_of_tick"] == 3
        assert detail.json()["as_of_seq"] > 0
        assert detail.json()["engine"]["projection_lag_ticks"] == 0
        assert len(agents.json()["items"]) == 8
        assert metric.json()["points"][-1]["value"] == 8
        assert not catalogue.json()["economy_available"]
        assert economy_metric.status_code == 501
        assert inspector.json()["recording"] in {"sampled", "not recorded"}
        assert mutation.status_code == 405
        assert compare.status_code == 501
        assert not compare.json()["available"]
    finally:
        engine_db = await Database.open(settings.store, role="engine")
        await engine_db.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
        await engine_db.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_observatory_serves_m2_economy_metrics_for_economy_runs() -> None:
    settings = load_settings(
        Path("configs/m2-smoke.yaml"),
        overrides={
            "run": {
                "name": "observatory-m2-test",
                "seed": 8_100_004,
                "ticks": 7,
                "scale": 8,
            },
            "population": {"initial_agents": 8},
        },
    )
    run_id = run_id_for(settings)
    engine_db = await Database.open(settings.store, role="engine")
    await engine_db.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
    await engine_db.close()
    try:
        await run_persistent(settings)
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                catalogue = await client.get(f"/api/v1/runs/{run_id}/metrics/catalogue")
                unemployment = await client.get(
                    f"/api/v1/runs/{run_id}/metrics",
                    params={"metric": "unemployment_rate"},
                )
                money = await client.get(
                    f"/api/v1/runs/{run_id}/metrics",
                    params={"metric": "m1"},
                )

        assert catalogue.status_code == 200
        assert catalogue.json()["economy_available"]
        assert unemployment.status_code == 200
        assert unemployment.json()["points"]
        assert money.status_code == 200
        assert money.json()["points"][-1]["value"] > 0
    finally:
        engine_db = await Database.open(settings.store, role="engine")
        await engine_db.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
        await engine_db.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_observatory_serves_m3_capital_projections() -> None:
    settings = load_settings(
        Path("configs/m3-smoke.yaml"),
        overrides={"run": {"name": "observatory-m3-test"}},
    )
    run_id = run_id_for(settings)
    engine_db = await Database.open(settings.store, role="engine")
    await engine_db.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
    await engine_db.close()
    try:
        await run_persistent(settings)
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                catalogue = await client.get(f"/api/v1/runs/{run_id}/metrics/catalogue")
                index = await client.get(
                    f"/api/v1/runs/{run_id}/metrics",
                    params={"metric": "market_index"},
                )
                securities = await client.get(f"/api/v1/runs/{run_id}/market/securities")
                trades = await client.get(f"/api/v1/runs/{run_id}/market/trades")
                startups = await client.get(f"/api/v1/runs/{run_id}/capital/startups")
                acquisitions = await client.get(f"/api/v1/runs/{run_id}/capital/acquisitions")
                bankruptcies = await client.get(f"/api/v1/runs/{run_id}/capital/bankruptcies")

        assert catalogue.status_code == 200
        assert catalogue.json()["capital_available"]
        assert index.status_code == 200
        assert index.json()["points"]
        assert len(securities.json()["items"]) == 1
        assert len(trades.json()["items"]) == 1
        assert len(startups.json()["items"]) == 2
        assert len(acquisitions.json()["items"]) == 1
        assert len(bankruptcies.json()["items"]) == 1
        assert bankruptcies.json()["items"][0]["status"] == "discharged"
    finally:
        engine_db = await Database.open(settings.store, role="engine")
        await engine_db.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
        await engine_db.close()

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from psycopg.types.json import Jsonb

from polis.config.canon import canonical_json, sha256_hex
from polis.config.mechanisms import mechanism_manifest
from polis.config.paths import repo_git_sha
from polis.config.runtime_time import utc_now_naive
from polis.config.settings import Settings, config_hash, config_yaml
from polis.living_city import LivingCityResult, run_living_city
from polis.observatory.live import RedisEphemeralPublisher
from polis.research.metrics import catalogue_manifest
from polis.simulation import run_id_for
from polis.store.engine import Database, StoreError
from polis.store.repositories.events import EventRepository
from polis.store.repositories.runs import RunRecord, RunRepository

_ACCOUNT_TYPES = {
    "cash": "cash",
    "dep": "deposit",
    "esc": "escrow",
    "res": "reserve",
    "lnr": "loan_receivable",
    "txr": "tax_receivable",
    "dpl": "deposit",
    "lnp": "loan_payable",
    "iss": "issuance",
}


def _prompt_manifest(settings: Settings) -> dict[str, str]:
    return {
        purpose: sha256_hex(route.template.encode())
        for purpose, route in sorted(settings.llm.routing.items())
    }


def _model_manifest(settings: Settings) -> dict[str, dict[str, str | None]]:
    return {
        purpose: {
            "lane": route.lane,
            "model": route.model,
            "provider_kind": settings.llm.providers[route.lane].kind,
            "model_version_pin": settings.llm.providers[route.lane].model_version_pin,
        }
        for purpose, route in sorted(settings.llm.routing.items())
    }


async def _clear_projections(db: Database, run_id: Any) -> None:
    for table in (
        "ledger_entries",
        "ledger_accounts",
        "banks",
        "firms",
        "cognition_traces",
        "metrics",
        "beliefs",
        "memories",
        "agents",
        "tiles",
        "places",
        "districts",
        "llm_calls",
        "engine_heartbeats",
    ):
        await db.execute(f"DELETE FROM {table} WHERE run_id=%s", (run_id,))


async def write_living_city_projections(
    db: Database,
    result: LivingCityResult,
    *,
    replace: bool = False,
    cache_mode: str = "hybrid",
) -> int:
    run_id = result.report.run_id
    if replace:
        await _clear_projections(db, run_id)
    as_of_seq = result.as_of_seq
    place_at = {(place.x, place.y): place.place_id for place in result.world.places}
    async with db.txn() as connection, connection.cursor() as cursor:
        await cursor.executemany(
            """
            INSERT INTO districts(run_id,district_id,name,polygon,properties)
            VALUES(%s,%s,%s,%s,%s)
            """,
            [
                (
                    run_id,
                    district.district_id,
                    district.name,
                    Jsonb({"bbox": district.bbox}),
                    Jsonb(
                        {
                            "archetype": district.archetype,
                            "land_value_cents": district.land_value_cents,
                            "school_quality": district.school_quality,
                            "crime_rate": district.crime_rate,
                            "amenity_score": district.amenity_score,
                        }
                    ),
                )
                for district in result.world.districts
            ],
        )
        await cursor.executemany(
            """
            INSERT INTO places(
                run_id,place_id,district_id,type,x,y,capacity,properties,
                name,owner_id,rent_cents,open_hours
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            [
                (
                    run_id,
                    place.place_id,
                    place.district_id,
                    place.type,
                    place.x,
                    place.y,
                    place.capacity,
                    Jsonb({}),
                    place.name,
                    place.owner_id,
                    place.rent_cents,
                    list(place.open_hours),
                )
                for place in result.world.places
            ],
        )
        await cursor.executemany(
            "INSERT INTO tiles(run_id,x,y,terrain,place_id) VALUES(%s,%s,%s,%s,%s)",
            [
                (
                    run_id,
                    x,
                    y,
                    int(result.world.terrain[y, x]),
                    place_at.get((x, y)),
                )
                for y in range(result.world.height)
                for x in range(result.world.width)
            ],
        )
        await cursor.executemany(
            """
            INSERT INTO agents(
                run_id,agent_id,born_tick,died_tick,age_years,district_id,place_id,
                state,as_of_tick,as_of_seq,display_name,kind,traits,needs,health,
                home_place_id,current_place_id,pos_x,pos_y,dest_place_id,path_cursor,
                education_level,employment_status,wealth_cents,reputation,
                reflex_profile,goals,cognition_mode
            ) VALUES(
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            [
                (
                    run_id,
                    agent.agent_id,
                    0,
                    None,
                    int(agent.age_years),
                    result.world.locations[agent.agent_id].district_id,
                    result.world.locations[agent.agent_id].place_id or "",
                    Jsonb(
                        {
                            "skills": agent.skills,
                            "identity_summary": agent.identity_summary,
                            "wellbeing": agent.wellbeing,
                        }
                    ),
                    result.report.last_tick,
                    as_of_seq,
                    agent.display_name,
                    "native",
                    Jsonb(agent.traits.as_dict()),
                    Jsonb(agent.needs.as_dict()),
                    agent.health,
                    agent.home_place_id,
                    result.world.locations[agent.agent_id].place_id,
                    result.world.locations[agent.agent_id].x,
                    result.world.locations[agent.agent_id].y,
                    result.world.locations[agent.agent_id].dest_place_id,
                    result.world.locations[agent.agent_id].path_cursor,
                    agent.education_level,
                    agent.employment_status,
                    agent.wealth_cents,
                    agent.reputation,
                    Jsonb(agent.reflex_profile.as_dict()),
                    Jsonb(agent.goals),
                    agent.cognition_mode,
                )
                for agent in result.population
            ],
        )
        if result.economy is not None:
            await cursor.executemany(
                """
                INSERT INTO ledger_accounts(
                    run_id,account_id,owner_id,owner_type,account_type,currency,
                    balance_cents,opened_tick,closed_tick
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        account.account_id,
                        account.owner_id,
                        account.owner_type,
                        _ACCOUNT_TYPES[account.code],
                        account.currency,
                        account.balance_cents,
                        account.opened_tick,
                        account.closed_tick,
                    )
                    for account in result.economy.ledger.accounts()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO ledger_entries(
                    run_id,txn_id,tick,account_id,direction,amount_cents,reason,event_seq
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        entry.txn_id,
                        entry.tick,
                        entry.account_id,
                        entry.direction,
                        entry.amount_cents,
                        entry.reason,
                        entry.event_seq,
                    )
                    for entry in result.economy.ledger.entries()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO firms(
                    run_id,firm_id,name,founded_tick,dissolved_tick,sector,place_id,
                    founder_id,ledger_account_id,productivity_bp,capital_cents,
                    headcount,is_public,symbol,status
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        firm.firm_id,
                        firm.name,
                        0,
                        None,
                        firm.sector,
                        firm.place_id,
                        firm.founder_id,
                        firm.ledger_account_id,
                        firm.productivity_bp,
                        firm.capital_cents,
                        firm.headcount,
                        False,
                        None,
                        firm.status,
                    )
                    for firm in result.economy.firms.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO banks(
                    run_id,bank_id,name,place_id,ledger_account_id,reserve_account_id,
                    deposit_liability_account_id,capital_cents,reserve_ratio_bp,
                    is_central,status,founded_tick,failed_tick
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        bank.bank_id,
                        bank.name,
                        bank.place_id,
                        bank.deposit_liability_account_id,
                        bank.reserve_account_id,
                        bank.deposit_liability_account_id,
                        bank.capital_cents,
                        bank.reserve_ratio_bp,
                        bank.is_central,
                        bank.status,
                        0,
                        None,
                    )
                    for bank in result.economy.banks.values()
                ],
            )
        memories = [
            row
            for agent in result.population
            for row in result.memory.for_agent(
                agent.agent_id,
                include_archived=True,
            )
        ]
        if memories:
            await cursor.executemany(
                """
                INSERT INTO memories(
                    memory_id,run_id,agent_id,tick,type,text,importance,
                    last_accessed_tick,parent_memory_ids,subject_ids,archived,
                    embedding,source_event_seq,access_count
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        row.memory_id,
                        run_id,
                        row.agent_id,
                        row.tick,
                        row.type,
                        row.text,
                        row.importance,
                        row.last_accessed_tick,
                        list(row.parent_memory_ids),
                        list(row.subject_ids),
                        row.archived,
                        None,
                        row.source_event_seq,
                        row.access_count,
                    )
                    for row in memories
                ],
            )
        if result.metrics.points:
            await cursor.executemany(
                """
                INSERT INTO metrics(run_id,tick,metric,value,as_of_seq)
                VALUES(%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        point.tick,
                        point.metric,
                        point.value,
                        point.as_of_seq,
                    )
                    for point in result.metrics.points
                ],
            )
        if result.traces:
            await cursor.executemany(
                """
                INSERT INTO cognition_traces(run_id,agent_id,tick,trace,as_of_seq)
                VALUES(%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        agent_id,
                        tick,
                        Jsonb(asdict(trace), dumps=canonical_json),
                        as_of_seq,
                    )
                    for (agent_id, tick), trace in sorted(result.traces.items())
                ],
            )
        calls: dict[str, tuple[Any, ...]] = {}
        for (agent_id, tick), trace in sorted(result.traces.items()):
            response = trace.response
            if not response or not response.get("call_id"):
                continue
            call_id = str(response["call_id"])
            prompt = trace.prompt or {}
            calls[call_id] = (
                call_id,
                run_id,
                tick,
                agent_id,
                response["purpose"],
                response["lane"],
                response["model"],
                prompt.get("prompt_hash", ""),
                Jsonb(
                    {
                        "template": prompt.get("template"),
                        "template_hash": prompt.get("template_hash"),
                    }
                ),
                Jsonb({"text": response["text"], "parsed_ok": response["parsed_ok"]}),
                response["cache_hit"],
                cache_mode,
                response["tokens_in"],
                response["tokens_out"],
                response["cost_usd"],
                response["latency_ms"],
                None,
                "cognition",
            )
        if calls:
            await cursor.executemany(
                """
                INSERT INTO llm_calls(
                    call_id,run_id,tick,actor_id,purpose,lane,model,prompt_hash,
                    request,response,cache_hit,cache_mode,tokens_in,tokens_out,
                    cost_usd,latency_ms,provider_request_id,budget_line
                ) VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                list(calls.values()),
            )
        await cursor.execute(
            """
            INSERT INTO engine_heartbeats(run_id,tick,as_of_seq,updated_at)
            VALUES(%s,%s,%s,CURRENT_TIMESTAMP)
            """,
            (run_id, result.report.last_tick, as_of_seq),
        )
    return as_of_seq


async def run_persistent(settings: Settings) -> LivingCityResult:
    db = await Database.open(settings.store, role="engine")
    run_id = run_id_for(settings)
    runs = RunRepository(db)
    try:
        if await runs.get(run_id) is not None:
            raise StoreError(f"run {run_id} already exists; change the config/seed or use replay")
        await runs.create(
            RunRecord(
                run_id=run_id,
                name=settings.run.name,
                config_yaml=config_yaml(settings),
                config_hash=config_hash(settings),
                master_seed=settings.run.seed,
                prompt_manifest=_prompt_manifest(settings),
                model_manifest=_model_manifest(settings),
                metric_manifest=catalogue_manifest(),
                mechanism_manifest=mechanism_manifest(settings),
                ablations=settings.ablations.model_dump(mode="json"),
                scale=settings.population.initial_agents,
                code_git_sha=repo_git_sha(),
                started_at=utc_now_naive(),
                status="running",
                tags=settings.run.tags,
            )
        )
        publisher = RedisEphemeralPublisher(
            settings.store.redis_url,
            run_id,
            rate_hz=settings.observatory.live.rate_hz,
        )
        await publisher.start()
        try:
            result = await run_living_city(
                settings,
                sink=EventRepository(db, run_id),
                ephemeral_sink=publisher,
                collect_events=False,
            )
        finally:
            await publisher.close()
        as_of_seq = await write_living_city_projections(
            db,
            result,
            cache_mode=settings.llm.cache.mode,
        )
        await db.execute(
            """
            UPDATE runs SET status=%s,ended_at=%s,last_tick=%s,terminal_hash=%s
            WHERE run_id=%s
            """,
            (
                result.report.status,
                utc_now_naive(),
                result.report.last_tick,
                result.report.chain_hash,
                run_id,
            ),
        )
        await db.execute(
            "UPDATE engine_heartbeats SET as_of_seq=%s WHERE run_id=%s",
            (as_of_seq, run_id),
        )
        return result
    except Exception:
        if await runs.get(run_id) is not None:
            await db.execute(
                """
                UPDATE runs SET status='failed',ended_at=%s
                WHERE run_id=%s AND status='running'
                """,
                (utc_now_naive(), run_id),
            )
        raise
    finally:
        await db.close()

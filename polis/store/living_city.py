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
        "cpi_series",
        "cpi_baskets",
        "goods_transactions",
        "agent_skills",
        "inventory",
        "employments",
        "job_offers",
        "job_applications",
        "vacancies",
        "skus",
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
                    liquid_cents,headcount,target_headcount,cumulative_output_units,
                    cumulative_revenue_cents,cumulative_wage_cents,is_public,symbol,status
                ) VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s
                )
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
                        firm.liquid_cents,
                        firm.headcount,
                        firm.target_headcount,
                        firm.cumulative_output_units,
                        firm.cumulative_revenue_cents,
                        firm.cumulative_wage_cents,
                        False,
                        None,
                        firm.status,
                    )
                    for firm in result.economy.firms.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO vacancies(
                    run_id,vacancy_id,firm_id,posted_tick,closed_tick,expires_tick,
                    district_id,occupation,skill_reqs,wage_offer_cents,headcount,
                    min_match_score_bp,applicants_n,status,filled_by
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        vacancy.vacancy_id,
                        vacancy.firm_id,
                        vacancy.posted_tick,
                        (result.report.last_tick if vacancy.status != "open" else None),
                        vacancy.expires_tick,
                        vacancy.district_id,
                        vacancy.occupation,
                        Jsonb(vacancy.skill_reqs),
                        vacancy.wage_offer_cents,
                        vacancy.headcount,
                        vacancy.min_match_score_bp,
                        vacancy.applicants_n,
                        vacancy.status,
                        None,
                    )
                    for vacancy in result.economy.vacancies.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO job_applications(
                    run_id,application_id,vacancy_id,agent_id,tick,
                    asked_wage_cents,outcome,match_score_bp,rank
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        application.application_id,
                        application.vacancy_id,
                        application.agent_id,
                        application.submitted_tick,
                        application.asked_wage_cents,
                        application.status,
                        application.match_score_bp,
                        application.rank,
                    )
                    for application in result.economy.applications.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO job_offers(
                    run_id,offer_id,application_id,vacancy_id,firm_id,agent_id,
                    wage_cents,occupation,made_tick,expires_tick,status
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        offer.offer_id,
                        offer.application_id,
                        offer.vacancy_id,
                        offer.firm_id,
                        offer.agent_id,
                        offer.wage_cents,
                        offer.occupation,
                        offer.made_tick,
                        offer.expires_tick,
                        offer.status,
                    )
                    for offer in result.economy.offers.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO employments(
                    run_id,employment_id,agent_id,firm_id,occupation,wage_cents,
                    started_tick,ended_tick,end_reason,match_score_bp,hours_bp,
                    accrued_wage_cents,accrual_remainder,total_paid_cents,
                    last_worked_tick,last_effective_labour_bp
                ) VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                [
                    (
                        run_id,
                        employment.employment_id,
                        employment.agent_id,
                        employment.firm_id,
                        employment.occupation,
                        employment.wage_cents,
                        employment.started_tick,
                        employment.ended_tick,
                        None,
                        employment.match_score_bp,
                        employment.hours_bp,
                        employment.accrued_wage_cents,
                        employment.accrual_remainder,
                        employment.total_paid_cents,
                        employment.last_worked_tick,
                        employment.last_effective_labour_bp,
                    )
                    for employment in result.economy.employments.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO skus(
                    run_id,sku,category,is_necessity,base_utility_bp,
                    perishable_bp_per_day,durable_life_ticks,is_service,is_capital,
                    need_restore_bp,gamma_units_per_year,beta_bp,sectors,yield_units
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        sku.sku,
                        sku.category,
                        sku.is_necessity,
                        sku.base_utility_bp,
                        sku.perishable_bp_per_day,
                        sku.durable_life_ticks,
                        sku.is_service,
                        sku.is_capital,
                        Jsonb(sku.need_restore_bp),
                        sku.gamma_units_per_year,
                        sku.beta_bp,
                        list(sku.sectors),
                        sku.yield_units,
                    )
                    for sku in result.economy.skus.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO inventory(
                    run_id,firm_id,sku,qty,unit_cost_cents,price_cents,carry_micro,
                    markup_bp,units_sold_28d,updated_tick
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        inventory.firm_id,
                        inventory.sku,
                        inventory.quantity,
                        inventory.unit_cost_cents,
                        inventory.price_cents,
                        inventory.carry_micro,
                        inventory.markup_bp,
                        inventory.units_sold_28d,
                        result.report.last_tick,
                    )
                    for inventory in result.economy.inventory.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO goods_transactions(
                    run_id,txn_id,ledger_txn_id,tick,buyer_id,seller_firm_id,sku,
                    qty,unit_price_cents,gross_cents,sales_tax_cents,subsidy_cents
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        transaction.txn_id,
                        transaction.ledger_txn_id,
                        transaction.tick,
                        transaction.buyer_id,
                        transaction.seller_firm_id,
                        transaction.sku,
                        transaction.qty,
                        transaction.unit_price_cents,
                        transaction.gross_cents,
                        transaction.sales_tax_cents,
                        transaction.subsidy_cents,
                    )
                    for transaction in result.economy.goods_transactions
                ],
            )
            if result.economy.basket is not None:
                await cursor.execute(
                    """
                    INSERT INTO cpi_baskets(
                        run_id,version,fixed_tick,quantities,base_prices_cents
                    ) VALUES(%s,%s,%s,%s,%s)
                    """,
                    (
                        run_id,
                        result.economy.basket.version,
                        result.economy.basket.fixed_tick,
                        Jsonb(result.economy.basket.quantities),
                        Jsonb(result.economy.basket.base_prices_cents),
                    ),
                )
            await cursor.executemany(
                """
                INSERT INTO cpi_series(
                    run_id,tick,index_bp,core_bp,fisher_bp,category_index_bp
                ) VALUES(%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        tick,
                        index_bp,
                        result.economy.cpi_core_history_bp.get(tick, index_bp),
                        result.economy.cpi_fisher_history_bp.get(tick, index_bp),
                        Jsonb(
                            {
                                category: values[tick]
                                for category, values in (
                                    result.economy.cpi_category_history_bp.items()
                                )
                                if tick in values
                            }
                        ),
                    )
                    for tick, index_bp in sorted(result.economy.cpi_history_bp.items())
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO agent_skills(
                    run_id,agent_id,skill,level_bp,last_used_tick
                ) VALUES(%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        agent.agent_id,
                        skill,
                        round(level * 10_000),
                        result.economy.skill_last_used_tick.get(
                            agent.agent_id,
                            {},
                        ).get(skill, 0),
                    )
                    for agent in result.population
                    for skill, level in sorted(agent.skills.items())
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

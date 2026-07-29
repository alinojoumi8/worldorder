from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from psycopg.types.json import Jsonb

from polis.config.canon import canonical_json
from polis.config.runtime_time import utc_now_naive
from polis.config.settings import Settings, config_yaml
from polis.events.kinds import (
    EXTERNAL_ACTION_REJECTED,
    EXTERNAL_ACTION_SUBMITTED,
    EXTERNAL_AGENT_NATURALISED,
    EXTERNAL_AGENT_REGISTERED,
    EXTERNAL_DEADLINE_MISSED,
    EXTERNAL_KEY_REVOKED,
    EXTERNAL_SESSION_CLOSED,
    EXTERNAL_SESSION_OPENED,
    EXTERNAL_SIM_AWARE_FLAGGED,
    ORDER_CANCELLED,
    ORDER_EXPIRED,
    ORDER_FILLED,
    ORDER_SUBMITTED,
)
from polis.external import ExternalDecisionPort
from polis.living_city import LivingCityResult, run_living_city
from polis.llm.cache import (
    EMPTY_COMPLETION_CACHE_MANIFEST_HASH,
    CompletionCache,
)
from polis.observatory.live import RedisEphemeralPublisher
from polis.run_identity import build_run_identity
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


async def _clear_projections(db: Database, run_id: Any) -> None:
    for table in (
        "external_latency",
        "external_nonces",
        "external_sessions",
        "external_agents",
        "bankruptcy_claims",
        "bankruptcies",
        "acquisitions",
        "term_sheets",
        "pitches",
        "cap_table",
        "funding_rounds",
        "vc_funds",
        "startups",
        "ipos",
        "short_positions",
        "ohlcv",
        "trades",
        "orders",
        "ledger_entries",
        "ledger_accounts",
        "holdings",
        "securities",
        "tax_assessments",
        "loan_payments",
        "loans",
        "loan_applications",
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
        "households",
        "agents",
        "tiles",
        "places",
        "districts",
        "llm_calls",
        "engine_heartbeats",
    ):
        await db.execute(f"DELETE FROM {table} WHERE run_id=%s", (run_id,))


def _coalesce(value: Any, default: Any) -> Any:
    return default if value is None else value


def _external_registration_row(
    actor_id: str,
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    declaration = dict(_coalesce(payload.get("declaration"), {}))
    return {
        "agent_id": actor_id,
        "pubkey": str(payload["pubkey"]),
        "operator": str(payload.get("operator") or declaration.get("operator") or ""),
        "contact": str(_coalesce(declaration.get("contact"), "")),
        "display_name": str(_coalesce(declaration.get("display_name"), actor_id)),
        "declared_model": str(payload.get("declared_model") or ""),
        "declared_model_version": str(_coalesce(declaration.get("declared_model_version"), "")),
        "declared_scaffold": str(payload.get("declared_scaffold") or ""),
        "scaffold_notes": str(_coalesce(declaration.get("scaffold_notes"), "")),
        "memory": str(_coalesce(declaration.get("memory"), "ours")),
        "sdk_version": str(_coalesce(declaration.get("sdk_version"), "")),
        "protocol_version": int(_coalesce(declaration.get("protocol_version"), 1)),
        "requested_embodiment": declaration.get("requested_embodiment"),
        "embodiment": str(payload["embodiment"]),
        "conformance_token": payload.get("conformance_token"),
        "twin_agent_id": payload.get("twin_agent_id"),
        "registered_tick": int(event["tick"]),
        "admitted_tick": int(payload["admitted_tick"]),
        "revoked_tick": None,
        "naturalised_tick": None,
        "resume_grace_until_tick": None,
        "consecutive_misses": 0,
        "ticks_driven": 0,
        "actions_submitted": 0,
        "actions_rejected": 0,
        "deadlines_missed": 0,
        "sim_aware_count": 0,
        "strikes": 0,
        "suspensions": 0,
    }


def _external_session_row(
    actor_id: str,
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    session_id = str(payload["session_id"])
    return {
        "session_id": session_id,
        "agent_id": actor_id,
        "custody": str(payload["custody"]),
        "delegate_pubkey": payload.get("delegate_pubkey"),
        "client": dict(_coalesce(payload.get("client"), {})),
        "opened_tick": int(event["tick"]),
        "expires_unix_ms": int(payload["expires_unix_ms"]),
        "closed_tick": None,
        "close_reason": None,
    }


def _registered_session_rows(
    session_rows: Mapping[str, dict[str, Any]],
    external_rows: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    registered_agent_ids = set(external_rows)
    return {
        session_id: row
        for session_id, row in session_rows.items()
        if row["agent_id"] in registered_agent_ids
    }


async def _fetch_gateway_events(
    db: Database,
    run_id: Any,
    as_of_seq: int,
) -> list[dict[str, Any]]:
    return await db.fetch(
        """
        SELECT kind,tick,actor_id,payload
        FROM events
        WHERE run_id=%s AND kind=ANY(%s::integer[]) AND seq<=%s
        ORDER BY tick,seq
        """,
        (
            run_id,
            [
                EXTERNAL_AGENT_REGISTERED,
                EXTERNAL_KEY_REVOKED,
                EXTERNAL_AGENT_NATURALISED,
                EXTERNAL_ACTION_SUBMITTED,
                EXTERNAL_ACTION_REJECTED,
                EXTERNAL_DEADLINE_MISSED,
                EXTERNAL_SIM_AWARE_FLAGGED,
                EXTERNAL_SESSION_OPENED,
                EXTERNAL_SESSION_CLOSED,
            ],
            as_of_seq,
        ),
    )


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
    gateway_events = await _fetch_gateway_events(db, run_id, as_of_seq)
    external_rows: dict[str, dict[str, Any]] = {}
    session_rows: dict[str, dict[str, Any]] = {}
    nonce_rows: dict[str, dict[str, int | str]] = {}
    for event in gateway_events:
        kind = int(event["kind"])
        actor_id = str(event["actor_id"] or "")
        payload = dict(event["payload"])
        if kind == EXTERNAL_AGENT_REGISTERED:
            external_rows[actor_id] = _external_registration_row(actor_id, event, payload)
            continue
        if kind == EXTERNAL_SESSION_OPENED:
            session_id = str(payload["session_id"])
            session_rows[session_id] = _external_session_row(actor_id, event, payload)
            continue
        if kind == EXTERNAL_SESSION_CLOSED:
            session_id = str(payload["session_id"])
            session = session_rows.get(session_id)
            if session is not None:
                session["closed_tick"] = int(event["tick"])
                session["close_reason"] = str(payload["reason"])
            continue
        row = external_rows.get(actor_id)
        if row is None:
            continue
        if kind == EXTERNAL_KEY_REVOKED:
            row["revoked_tick"] = int(event["tick"])
            row["strikes"] = int(_coalesce(payload.get("strikes"), 0))
        elif kind == EXTERNAL_AGENT_NATURALISED:
            row["naturalised_tick"] = int(event["tick"])
            row["resume_grace_until_tick"] = payload.get("resume_grace_until_tick")
            row["consecutive_misses"] = int(_coalesce(payload.get("consecutive_misses"), 0))
            row["ticks_driven"] = int(_coalesce(payload.get("ticks_driven"), 0))
        elif kind == EXTERNAL_ACTION_SUBMITTED:
            row["actions_submitted"] += 1
            row["ticks_driven"] += 1
            row["consecutive_misses"] = 0
            nonce_rows[actor_id] = {
                "agent_id": actor_id,
                "last_nonce": int(payload["nonce"]),
                "updated_tick": int(event["tick"]),
            }
        elif kind == EXTERNAL_ACTION_REJECTED:
            row["actions_rejected"] += 1
        elif kind == EXTERNAL_DEADLINE_MISSED:
            row["deadlines_missed"] += 1
            row["ticks_driven"] += 1
            row["consecutive_misses"] = int(_coalesce(payload.get("consecutive_misses"), 0))
        elif kind == EXTERNAL_SIM_AWARE_FLAGGED:
            row["sim_aware_count"] += 1
    session_rows = _registered_session_rows(session_rows, external_rows)
    place_at = {(place.x, place.y): place.place_id for place in result.world.places}
    submitted_seq = {
        str(event.payload["order_id"]): event.seq
        for event in result.events
        if event.kind == ORDER_SUBMITTED and "order_id" in event.payload
    }
    ended_tick = {
        str(event.payload["order_id"]): event.tick
        for event in result.events
        if event.kind in {ORDER_CANCELLED, ORDER_EXPIRED, ORDER_FILLED}
        and "order_id" in event.payload
    }

    def projected_location(
        agent_id: str, home_place_id: str
    ) -> tuple[str, str, int, int, str | None, int | None]:
        location = result.world.locations.get(agent_id)
        if location is not None:
            return (
                location.district_id,
                location.place_id or home_place_id,
                location.x,
                location.y,
                location.dest_place_id,
                location.path_cursor,
            )
        home = result.world.place(home_place_id)
        return home.district_id, home.place_id, home.x, home.y, None, 0

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
                state,as_of_tick,as_of_seq,display_name,kind,pubkey,traits,needs,health,
                home_place_id,current_place_id,pos_x,pos_y,dest_place_id,path_cursor,
                education_level,employment_status,wealth_cents,reputation,
                reflex_profile,goals,cognition_mode,household_id,mother_id,
                father_id,generation,died_at_tick,death_cause
            ) VALUES(
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            [
                (
                    run_id,
                    agent.agent_id,
                    agent.born_tick,
                    agent.died_at_tick,
                    int(agent.age_years),
                    location[0],
                    location[1],
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
                    agent.kind,
                    agent.pubkey,
                    Jsonb(agent.traits.as_dict()),
                    Jsonb(agent.needs.as_dict()),
                    agent.health,
                    agent.home_place_id,
                    location[1],
                    location[2],
                    location[3],
                    location[4],
                    location[5],
                    agent.education_level,
                    agent.employment_status,
                    agent.wealth_cents,
                    agent.reputation,
                    Jsonb(agent.reflex_profile.as_dict()),
                    Jsonb(agent.goals),
                    agent.cognition_mode,
                    agent.household_id,
                    agent.mother_id,
                    agent.father_id,
                    agent.generation,
                    agent.died_at_tick,
                    agent.death_cause,
                )
                for agent in result.population
                for location in (projected_location(agent.agent_id, agent.home_place_id),)
            ],
        )
        if external_rows:
            await cursor.executemany(
                """
                INSERT INTO external_agents(
                    run_id,agent_id,pubkey,operator,contact,display_name,
                    declared_model,declared_model_version,declared_scaffold,
                    scaffold_notes,memory,sdk_version,protocol_version,
                    requested_embodiment,embodiment,conformance_token,twin_agent_id,
                    registered_tick,admitted_tick,revoked_tick,naturalised_tick,
                    resume_grace_until_tick,consecutive_misses,ticks_driven,
                    actions_submitted,actions_rejected,deadlines_missed,
                    sim_aware_count,strikes,suspensions
                ) VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                [
                    (
                        run_id,
                        row["agent_id"],
                        row["pubkey"],
                        row["operator"],
                        row["contact"],
                        row["display_name"],
                        row["declared_model"],
                        row["declared_model_version"],
                        row["declared_scaffold"],
                        row["scaffold_notes"],
                        row["memory"],
                        row["sdk_version"],
                        row["protocol_version"],
                        row["requested_embodiment"],
                        row["embodiment"],
                        row["conformance_token"],
                        row["twin_agent_id"],
                        row["registered_tick"],
                        row["admitted_tick"],
                        row["revoked_tick"],
                        row["naturalised_tick"],
                        row["resume_grace_until_tick"],
                        row["consecutive_misses"],
                        row["ticks_driven"],
                        row["actions_submitted"],
                        row["actions_rejected"],
                        row["deadlines_missed"],
                        row["sim_aware_count"],
                        row["strikes"],
                        row["suspensions"],
                    )
                    for row in external_rows.values()
                ],
            )
        if session_rows:
            await cursor.executemany(
                """
                INSERT INTO external_sessions(
                    run_id,session_id,agent_id,custody,delegate_pubkey,client,
                    opened_tick,expires_unix_ms,closed_tick,close_reason
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        row["session_id"],
                        row["agent_id"],
                        row["custody"],
                        row["delegate_pubkey"],
                        Jsonb(row["client"]),
                        row["opened_tick"],
                        row["expires_unix_ms"],
                        row["closed_tick"],
                        row["close_reason"],
                    )
                    for row in session_rows.values()
                ],
            )
        if nonce_rows:
            await cursor.executemany(
                """
                INSERT INTO external_nonces(
                    run_id,agent_id,last_nonce,updated_tick
                ) VALUES(%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        row["agent_id"],
                        row["last_nonce"],
                        row["updated_tick"],
                    )
                    for row in nonce_rows.values()
                ],
            )
        if result.demography is not None:
            await cursor.executemany(
                """
                INSERT INTO households(
                    run_id,household_id,formed_at_tick,dissolved_at_tick,
                    home_place_id,member_ids,head_agent_id,tenure,rent_cents,
                    joint_baseline_cents,arrears_cents,as_of_tick,as_of_seq
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        household.household_id,
                        household.formed_at_tick,
                        household.dissolved_at_tick,
                        household.home_place_id,
                        list(household.member_ids),
                        household.head_agent_id,
                        household.tenure,
                        household.rent_cents,
                        Jsonb(dict(household.joint_baseline_cents)),
                        household.arrears_cents,
                        result.report.last_tick,
                        as_of_seq,
                    )
                    for household in sorted(
                        result.demography.households.households.values(),
                        key=lambda row: row.household_id,
                    )
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
                        firm.dissolved_tick,
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
                    is_central,status,founded_tick,failed_tick,lending_frozen,
                    capital_ratio_bp
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                        bank.founded_tick,
                        bank.failed_tick,
                        bank.lending_frozen,
                        bank.capital_ratio_bp,
                    )
                    for bank in result.economy.banks.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO loan_applications(
                    run_id,application_id,borrower_id,lender_id,requested_cents,
                    purpose,term_ticks,collateral,submitted_tick,status,score_bp,
                    offered_cents,offered_rate_bp,reason_codes
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        application.application_id,
                        application.borrower_id,
                        application.lender_id,
                        application.requested_cents,
                        application.purpose,
                        application.term_ticks,
                        Jsonb(application.collateral),
                        application.submitted_tick,
                        application.status,
                        application.score_bp,
                        application.offered_cents,
                        application.offered_rate_bp,
                        Jsonb(list(application.reason_codes)),
                    )
                    for application in result.economy.loan_applications.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO loans(
                    run_id,loan_id,lender_id,borrower_id,purpose,principal_cents,
                    outstanding_cents,annual_rate_bp,term_ticks,originated_tick,
                    matures_tick,status,collateral,collateral_value_cents,
                    credit_score_at_origination_bp,payment_cents,payments_n,
                    next_payment_tick,accrued_interest_cents,
                    total_interest_paid_cents,capitalised_interest_cents,
                    missed_since_tick,defaulted_tick,closed_tick
                ) VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s
                )
                """,
                [
                    (
                        run_id,
                        loan.loan_id,
                        loan.lender_id,
                        loan.borrower_id,
                        loan.purpose,
                        loan.principal_cents,
                        loan.outstanding_cents,
                        loan.annual_rate_bp,
                        loan.term_ticks,
                        loan.originated_tick,
                        loan.matures_tick,
                        loan.status,
                        Jsonb(loan.collateral),
                        loan.collateral_value_cents,
                        loan.credit_score_at_origination_bp,
                        loan.payment_cents,
                        loan.payments_n,
                        loan.next_payment_tick,
                        loan.accrued_interest_cents,
                        loan.total_interest_paid_cents,
                        loan.capitalised_interest_cents,
                        loan.missed_since_tick,
                        loan.defaulted_tick,
                        loan.closed_tick,
                    )
                    for loan in result.economy.loans.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO loan_payments(
                    run_id,payment_id,loan_id,tick,principal_cents,interest_cents,missed
                ) VALUES(%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        payment.payment_id,
                        payment.loan_id,
                        payment.tick,
                        payment.principal_cents,
                        payment.interest_cents,
                        payment.missed,
                    )
                    for payment in result.economy.loan_payments
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO tax_assessments(
                    run_id,assessment_id,taxpayer_id,tax_type,base_cents,rate_bp,
                    assessed_cents,assessed_tick,due_tick,paid_cents,status
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        assessment.assessment_id,
                        assessment.taxpayer_id,
                        assessment.tax_type,
                        assessment.base_cents,
                        assessment.rate_bp,
                        assessment.assessed_cents,
                        assessment.assessed_tick,
                        assessment.due_tick,
                        assessment.paid_cents,
                        assessment.status,
                    )
                    for assessment in result.economy.tax_assessments.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO securities(
                    run_id,symbol,issuer_firm_id,class,shares_outstanding,
                    listed_tick,delisted_tick,coupon_bp,matures_tick
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        bond.symbol,
                        "gv_treasury",
                        "bond",
                        bond.face_cents,
                        bond.issued_tick,
                        bond.matures_tick if bond.status == "matured" else None,
                        bond.coupon_bp,
                        bond.matures_tick,
                    )
                    for bond in result.economy.bonds.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO holdings(
                    run_id,holder_id,symbol,qty,avg_cost_cents,reserved_qty
                ) VALUES(%s,%s,%s,%s,%s,%s)
                """,
                [
                    (run_id, holder_id, symbol, 1, cents, 0)
                    for holder_id, holdings in result.economy.bond_holdings_cents.items()
                    for symbol, cents in holdings.items()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO securities(
                    run_id,symbol,issuer_firm_id,class,shares_outstanding,
                    listed_tick,delisted_tick,coupon_bp,matures_tick,
                    listing_price_cents,last_price_cents,reference_price_cents,
                    ipo_round_id,lockup_until_tick,status,halt_until_tick,
                    breaker_count
                ) VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                [
                    (
                        run_id,
                        security.symbol,
                        security.issuer_firm_id,
                        security.security_class,
                        security.shares_outstanding,
                        security.listed_tick,
                        security.delisted_tick,
                        None,
                        None,
                        security.listing_price_cents,
                        security.last_price_cents,
                        security.reference_price_cents,
                        security.ipo_round_id,
                        security.lockup_until_tick,
                        security.status,
                        security.halt_until_tick,
                        security.breaker_count,
                    )
                    for security in result.economy.exchange.securities.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO holdings(
                    run_id,holder_id,symbol,qty,avg_cost_cents,reserved_qty,
                    locked_qty
                ) VALUES(%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        holding.holder_id,
                        holding.symbol,
                        holding.qty,
                        holding.avg_cost_cents,
                        holding.reserved_qty,
                        holding.locked_qty,
                    )
                    for holding in result.economy.exchange.holdings.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO orders(
                    run_id,order_id,symbol,trader_id,side,order_type,
                    limit_price_cents,qty,remaining_qty,filled_qty,status,
                    submitted_tick,submitted_seq,ended_tick,arrival_ordinal,
                    reserved_cents,reserved_qty,filled_notional_cents,
                    commission_cents,flags
                ) VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s
                )
                """,
                [
                    (
                        run_id,
                        order.order_id,
                        order.symbol,
                        order.trader_id,
                        order.side,
                        order.order_type,
                        order.limit_price_cents,
                        order.qty,
                        order.remaining_qty,
                        order.filled_qty,
                        order.status,
                        order.submitted_tick,
                        submitted_seq.get(order.order_id, order.arrival_ordinal),
                        ended_tick.get(order.order_id),
                        order.arrival_ordinal,
                        order.reserved_cents,
                        order.reserved_qty,
                        order.filled_notional_cents,
                        order.commission_cents,
                        Jsonb(list(order.flags)),
                    )
                    for order in result.economy.exchange.orders.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO trades(
                    run_id,trade_id,tick,symbol,price_cents,qty,buy_order_id,
                    sell_order_id,buyer_id,seller_id,aggressor,
                    commission_buy_cents,commission_sell_cents,ledger_txn_id
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        trade.trade_id,
                        trade.tick,
                        trade.symbol,
                        trade.price_cents,
                        trade.qty,
                        trade.buy_order_id,
                        trade.sell_order_id,
                        trade.buyer_id,
                        trade.seller_id,
                        trade.aggressor,
                        trade.commission_buy_cents,
                        trade.commission_sell_cents,
                        trade.ledger_txn_id,
                    )
                    for trade in result.economy.exchange.trades
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO ohlcv(
                    run_id,symbol,session_tick,open_cents,high_cents,low_cents,
                    close_cents,volume,vwap_cents,trades_n
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        row.symbol,
                        row.session_tick,
                        row.open_cents,
                        row.high_cents,
                        row.low_cents,
                        row.close_cents,
                        row.volume,
                        row.vwap_cents,
                        row.trades_n,
                    )
                    for row in result.economy.exchange.ohlcv.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO short_positions(
                    run_id,trader_id,symbol,qty,entry_price_cents,
                    collateral_cents,opened_tick,borrow_fee_bp,
                    margin_deadline_tick,status
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        row.trader_id,
                        row.symbol,
                        row.qty,
                        row.entry_price_cents,
                        row.collateral_cents,
                        row.opened_tick,
                        row.borrow_fee_bp,
                        row.margin_deadline_tick,
                        row.status,
                    )
                    for row in result.economy.exchange.shorts.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO ipos(
                    run_id,ipo_id,firm_id,symbol,shares_offered,
                    primary_shares,secondary_shares,price_low_cents,
                    price_high_cents,underwriter_bank_id,announced_tick,
                    book_close_tick,indications,status
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        row.ipo_id,
                        row.firm_id,
                        row.symbol,
                        row.shares_offered,
                        row.primary_shares,
                        row.secondary_shares,
                        row.price_low_cents,
                        row.price_high_cents,
                        row.underwriter_bank_id,
                        row.announced_tick,
                        row.book_close_tick,
                        Jsonb(row.indications),
                        row.status,
                    )
                    for row in result.economy.exchange.ipos.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO startups(
                    run_id,startup_id,firm_id,founder_id,thesis,sector,
                    founded_tick,initial_capital_cents,burn_rate_cents,
                    runway_ticks,revenue_ttm_cents,total_raised_cents,stage,status
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        row.startup_id,
                        row.firm_id,
                        row.founder_id,
                        row.thesis,
                        row.sector,
                        row.founded_tick,
                        row.initial_capital_cents,
                        row.burn_rate_cents,
                        row.runway_ticks,
                        row.revenue_ttm_cents,
                        row.total_raised_cents,
                        row.stage,
                        row.status,
                    )
                    for row in result.economy.ventures.startups.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO vc_funds(
                    run_id,fund_id,firm_id,gp_agent_id,committed_cents,
                    called_cents,deployed_cents,vintage_tick,thesis,
                    management_fee_bp,carry_bp,hurdle_bp,lps,status
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        row.fund_id,
                        row.firm_id,
                        row.gp_agent_id,
                        row.committed_cents,
                        row.called_cents,
                        row.deployed_cents,
                        row.vintage_tick,
                        row.thesis,
                        row.management_fee_bp,
                        row.carry_bp,
                        row.hurdle_bp,
                        Jsonb(row.lps),
                        row.status,
                    )
                    for row in result.economy.ventures.funds.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO funding_rounds(
                    run_id,round_id,startup_id,stage,pre_money_cents,
                    amount_cents,post_money_cents,price_per_share_cents,
                    new_shares,lead_investor_id,participants,option_pool_shares,
                    liq_pref_bp,participating,closed_tick
                ) VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                [
                    (
                        run_id,
                        row.round_id,
                        row.startup_id,
                        row.stage,
                        row.pre_money_cents,
                        row.amount_cents,
                        row.post_money_cents,
                        row.price_per_share_cents,
                        row.new_shares,
                        row.lead_investor_id,
                        Jsonb(row.participants),
                        row.option_pool_shares,
                        row.liq_pref_bp,
                        row.participating,
                        row.closed_tick,
                    )
                    for row in result.economy.ventures.rounds.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO cap_table(
                    run_id,firm_id,holder_id,share_class,shares,invested_cents,
                    round_id,liq_pref_bp,participating,pro_rata,
                    conversion_price_cents
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        row.firm_id,
                        row.holder_id,
                        row.share_class,
                        row.shares,
                        row.invested_cents,
                        row.round_id,
                        row.liq_pref_bp,
                        row.participating,
                        row.pro_rata,
                        row.conversion_price_cents,
                    )
                    for row in result.economy.ventures.cap_table.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO pitches(
                    run_id,pitch_id,startup_id,founder_id,investor_id,ask_cents,
                    pre_money_ask_cents,deck_text,made_tick,status,conviction_bp,
                    valuation_view_cents,verdict
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        row.pitch_id,
                        row.startup_id,
                        row.founder_id,
                        row.investor_id,
                        row.ask_cents,
                        row.pre_money_ask_cents,
                        row.deck_text,
                        row.made_tick,
                        row.status,
                        row.conviction_bp,
                        row.valuation_view_cents,
                        row.verdict,
                    )
                    for row in result.economy.ventures.pitches.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO term_sheets(
                    run_id,term_sheet_id,startup_id,investor_id,
                    pre_money_cents,amount_cents,security,liq_pref_bp,
                    participating,pro_rata,board_seat,option_pool_bp,
                    anti_dilution,issued_tick,expires_tick,status
                ) VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                [
                    (
                        run_id,
                        row.term_sheet_id,
                        row.startup_id,
                        row.investor_id,
                        row.pre_money_cents,
                        row.amount_cents,
                        row.security,
                        row.liq_pref_bp,
                        row.participating,
                        row.pro_rata,
                        row.board_seat,
                        row.option_pool_bp,
                        row.anti_dilution,
                        row.issued_tick,
                        row.expires_tick,
                        row.status,
                    )
                    for row in result.economy.ventures.term_sheets.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO acquisitions(
                    run_id,deal_id,acquirer_id,target_id,offer_cents,
                    per_share_cents,consideration,stock_ratio_bp,premium_bp,
                    integration_mode,financing,proposed_tick,expires_tick,
                    accepting_holders,status
                ) VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                [
                    (
                        run_id,
                        row.deal_id,
                        row.acquirer_id,
                        row.target_id,
                        row.offer_cents,
                        row.per_share_cents,
                        row.consideration,
                        row.stock_ratio_bp,
                        row.premium_bp,
                        row.integration_mode,
                        row.financing,
                        row.proposed_tick,
                        row.expires_tick,
                        Jsonb(row.accepting_holders),
                        row.status,
                    )
                    for row in result.economy.ventures.acquisitions.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO bankruptcies(
                    run_id,case_id,entity_id,entity_type,trigger,assets_cents,
                    liabilities_cents,filed_tick,stay_until_tick,status,
                    liquidation_tick,estate_cents,resolved_tick
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        row.case_id,
                        row.entity_id,
                        row.entity_type,
                        row.trigger,
                        row.assets_cents,
                        row.liabilities_cents,
                        row.filed_tick,
                        row.stay_until_tick,
                        row.status,
                        row.liquidation_tick,
                        row.estate_cents,
                        row.resolved_tick,
                    )
                    for row in result.economy.ventures.bankruptcies.values()
                ],
            )
            await cursor.executemany(
                """
                INSERT INTO bankruptcy_claims(
                    run_id,claim_id,case_id,creditor_id,claim_cents,
                    priority_class,collateral_ref,loan_id,paid_cents
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        row.claim_id,
                        row.case_id,
                        row.creditor_id,
                        row.claim_cents,
                        row.priority_class,
                        row.collateral_ref,
                        row.loan_id,
                        row.paid_cents,
                    )
                    for row in result.economy.ventures.claims.values()
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


async def run_persistent(
    settings: Settings,
    *,
    external_decisions: ExternalDecisionPort | None = None,
) -> LivingCityResult:
    db = await Database.open(settings.store, role="engine")
    run_id = run_id_for(settings)
    runs = RunRepository(db)
    completion_cache: CompletionCache | None = None
    try:
        if await runs.get(run_id) is not None:
            raise StoreError(f"run {run_id} already exists; change the config/seed or use replay")
        completion_cache = CompletionCache(
            mode=settings.llm.cache.mode,
            l0_entries=settings.llm.cache.l0_entries,
            verify_render=settings.llm.cache.verify_render,
            path=settings.llm.cache.path,
            namespace=str(run_id),
            schema_version=settings.llm.cache.schema_version,
            strict_version=settings.llm.cache.strict_version,
        )
        identity = build_run_identity(
            settings,
            completion_cache_manifest_hash=completion_cache.manifest_hash(),
        )
        await runs.create(
            RunRecord(
                run_id=run_id,
                name=settings.run.name,
                config_yaml=config_yaml(settings),
                config_hash=identity.config_hash,
                master_seed=identity.master_seed,
                prompt_manifest=identity.prompt_manifest,
                model_manifest=identity.model_manifest,
                metric_manifest=identity.metric_manifest,
                mechanism_manifest=identity.mechanism_manifest,
                completion_cache_manifest=completion_cache.manifest(),
                completion_cache_manifest_hash=identity.completion_cache_manifest_hash,
                ablations=settings.ablations.model_dump(mode="json"),
                scale=identity.scale,
                code_git_sha=identity.code_git_sha,
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
                completion_cache=completion_cache,
                external_decisions=external_decisions,
                run_identity=identity,
            )
        finally:
            await publisher.close()
        as_of_seq = await write_living_city_projections(
            db,
            result,
            cache_mode=settings.llm.cache.mode,
        )
        if external_decisions is not None:
            latency_rows = tuple(external_decisions.latency_rows())
            if latency_rows:
                async with db.txn() as connection, connection.cursor() as cursor:
                    await cursor.executemany(
                        """
                        INSERT INTO external_latency(
                            run_id,agent_id,tick,observation_pushed_ms,
                            action_received_ms,decision_ms,missed
                        ) VALUES(%s,%s,%s,%s,%s,%s,%s)
                        """,
                        [
                            (
                                run_id,
                                row.agent_id,
                                row.tick,
                                row.observation_pushed_ms,
                                row.action_received_ms,
                                row.decision_ms,
                                row.missed,
                            )
                            for row in latency_rows
                        ],
                    )
                external_decisions.clear_latency_rows()
            await db.execute(
                """
                UPDATE runs
                SET tags=array_append(
                    COALESCE(tags, '{}'::text[]),
                    'invalid_for_cross_agent_comparison'
                )
                WHERE run_id=%s
                  AND NOT (
                      'invalid_for_cross_agent_comparison'
                      =ANY(COALESCE(tags, '{}'::text[]))
                  )
                  AND EXISTS (
                      SELECT 1 FROM external_agents
                      WHERE run_id=%s
                        AND ticks_driven > 0
                        AND deadlines_missed::double precision / ticks_driven > %s
                  )
                """,
                (
                    run_id,
                    run_id,
                    settings.research.gates.external_miss_rate_max,
                ),
            )
            await db.execute(
                """
                UPDATE runs
                SET tags=array_append(
                    COALESCE(tags, '{}'::text[]),
                    'custody_delegated'
                )
                WHERE run_id=%s
                  AND NOT (
                      'custody_delegated'=ANY(COALESCE(tags, '{}'::text[]))
                  )
                  AND EXISTS (
                      SELECT 1 FROM external_sessions
                      WHERE run_id=%s AND custody='delegated'
                  )
                """,
                (run_id, run_id),
            )
        await db.execute(
            """
            UPDATE runs
            SET status=%s,ended_at=%s,last_tick=%s,terminal_hash=%s,
                completion_cache_manifest=%s,completion_cache_manifest_hash=%s
            WHERE run_id=%s AND status='running'
            """,
            (
                result.report.status,
                utc_now_naive(),
                result.report.last_tick,
                result.report.chain_hash,
                Jsonb(result.completion_cache_manifest),
                result.completion_cache_manifest_hash,
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
            manifest = completion_cache.manifest() if completion_cache is not None else {}
            manifest_hash = (
                completion_cache.manifest_hash()
                if completion_cache is not None
                else EMPTY_COMPLETION_CACHE_MANIFEST_HASH
            )
            await db.execute(
                """
                UPDATE runs
                SET status='failed',ended_at=%s,
                    completion_cache_manifest=%s,completion_cache_manifest_hash=%s
                WHERE run_id=%s AND status='running'
                """,
                (utc_now_naive(), Jsonb(manifest), manifest_hash, run_id),
            )
        raise
    finally:
        if completion_cache is not None:
            await completion_cache.close()
        await db.close()

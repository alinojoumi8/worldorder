from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from polis.agents.state import AgentPopulation
from polis.config.settings import Settings
from polis.economy.credit import CreditContext, capital_cents, rwa_cents
from polis.economy.invariants import m0_cents, m1_cents
from polis.economy.labour import labour_force
from polis.economy.state import EconomyState


def _period_ticks(settings: Settings, days: int) -> int:
    return days * settings.clock.ticks_per_sim_day


def _due(tick: int, period_ticks: int) -> bool:
    return period_ticks > 0 and tick % period_ticks == 0


def _integer_median(values: Sequence[int]) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _gini_bp(values: Sequence[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    total = sum(ordered)
    if total <= 0:
        return None
    numerator = sum(
        (2 * position - len(ordered) - 1) * value for position, value in enumerate(ordered, start=1)
    )
    return 10_000 * numerator // (len(ordered) * total)


def _wealth_shares_bp(values: Sequence[int]) -> dict[str, int] | None:
    ordered = sorted(values)
    total = sum(ordered)
    if not ordered or total <= 0:
        return None
    count = len(ordered)
    top_one_n = max(1, (count + 99) // 100)
    top_ten_n = max(1, (count + 9) // 10)
    bottom_half_n = max(1, count // 2)
    return {
        "wealth_share.top1": 10_000 * sum(ordered[-top_one_n:]) // total,
        "wealth_share.top10": 10_000 * sum(ordered[-top_ten_n:]) // total,
        "wealth_share.bottom50": 10_000 * sum(ordered[:bottom_half_n]) // total,
    }


def _inventory_value_cents(economy: EconomyState) -> int:
    return sum(row.quantity * row.unit_cost_cents for row in economy.inventory.values())


def _transaction_flows(
    economy: EconomyState,
    population_ids: frozenset[str],
    *,
    from_tick: int,
    to_tick: int,
) -> tuple[int, int, int, int, dict[str, int]]:
    consumption = 0
    investment = 0
    government = 0
    intermediates = 0
    revenue_by_firm: dict[str, int] = defaultdict(int)
    for transaction in economy.goods_transactions:
        if not from_tick <= transaction.tick <= to_tick:
            continue
        revenue_by_firm[transaction.seller_firm_id] += transaction.gross_cents
        if transaction.buyer_id in population_ids:
            consumption += transaction.gross_cents
        elif transaction.buyer_id in economy.firms:
            sku = economy.skus[transaction.sku]
            if sku.is_capital:
                investment += transaction.gross_cents
            else:
                intermediates += transaction.gross_cents
        elif transaction.buyer_id.startswith("gv_"):
            government += transaction.gross_cents
    return consumption, investment, government, intermediates, dict(revenue_by_firm)


def _hhi_sector_bp(economy: EconomyState, revenue_by_firm: Mapping[str, int]) -> int | None:
    sector_firms: dict[str, list[int]] = defaultdict(list)
    for firm_id, revenue in revenue_by_firm.items():
        if revenue > 0 and firm_id in economy.firms:
            sector_firms[economy.firms[firm_id].sector].append(revenue)
    total_revenue = sum(sum(values) for values in sector_firms.values())
    if total_revenue <= 0:
        return None
    weighted = 0
    for values in sector_firms.values():
        sector_total = sum(values)
        hhi = 10_000 * sum(value * value for value in values) // (sector_total * sector_total)
        weighted += hhi * sector_total
    return weighted // total_revenue


def _income_by_agent(
    history: Mapping[int, Mapping[str, int]],
    *,
    from_tick: int,
    to_tick: int,
) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for tick, rows in history.items():
        if from_tick <= tick <= to_tick:
            for agent_id, cents in rows.items():
                result[agent_id] += cents
    return dict(result)


def economy_metric_values(
    settings: Settings,
    population: AgentPopulation,
    economy: EconomyState,
    *,
    tick: int,
    previous_inventory_cents: int | None,
    inventory_year_ago_cents: int | None,
    credit_year_ago_cents: int | None,
) -> dict[str, float]:
    """Calculate only metrics whose declared simulation cadence is due."""
    day = _period_ticks(settings, 1)
    week = _period_ticks(settings, 7)
    quarter = _period_ticks(settings, settings.clock.days_per_sim_year // 4)
    year = _period_ticks(settings, settings.clock.days_per_sim_year)
    values: dict[str, float] = {
        "m0": float(m0_cents(economy.ledger)),
        "m1": float(m1_cents(economy.ledger)),
    }
    population_ids = frozenset(agent.agent_id for agent in population)

    if _due(tick, day):
        force = labour_force(
            population,
            economy,
            tick=tick,
            search_window_ticks=settings.labour.search_window_days * day,
            retirement_age=settings.labour.retirement_age,
        )
        cpi_ticks = [row_tick for row_tick in economy.cpi_history_bp if row_tick <= tick]
        current_cpi = economy.cpi_history_bp[max(cpi_ticks)] if cpi_ticks else 10_000
        credit_context = CreditContext(settings, population, economy)
        system_capital = sum(
            capital_cents(bank.bank_id, economy)
            for bank in economy.banks.values()
            if not bank.is_central and bank.status == "active"
        )
        system_rwa = sum(
            rwa_cents(bank.bank_id, credit_context)
            for bank in economy.banks.values()
            if not bank.is_central and bank.status == "active"
        )
        values.update(
            {
                "unemployment_rate": float(force.unemployment_bp),
                "u_broad": float(force.unemployment_broad_bp),
                "lfpr": float(force.participation_bp),
                "vacancy_rate": float(force.vacancy_rate_bp),
                "cpi": float(current_cpi),
                "bank_capital_ratio": float(
                    10_000 * system_capital // system_rwa if system_rwa > 0 else 10_000
                ),
                "policy_rate_bp": float(economy.policy_rate_bp),
            }
        )

    live_loans = [
        loan
        for loan in economy.loans.values()
        if loan.status in {"current", "delinquent", "default"} and loan.outstanding_cents > 0
    ]
    outstanding = sum(loan.outstanding_cents for loan in live_loans)
    if _due(tick, week):
        open_wages = [
            employment.wage_cents
            for employment in economy.employments.values()
            if employment.started_tick <= tick
            and (employment.ended_tick is None or employment.ended_tick > tick)
        ]
        lending_rate = (
            sum(loan.outstanding_cents * loan.annual_rate_bp for loan in live_loans) // outstanding
            if outstanding
            else economy.policy_rate_bp
        )
        window_start = max(0, tick - week + 1)
        defaults = sum(
            loan.defaulted_tick is not None and window_start <= loan.defaulted_tick <= tick
            for loan in economy.loans.values()
        )
        current_at_start = sum(
            loan.originated_tick <= window_start
            and (loan.defaulted_tick is None or loan.defaulted_tick > window_start)
            and (loan.closed_tick is None or loan.closed_tick > window_start)
            for loan in economy.loans.values()
        )
        values.update(
            {
                "median_wage": float(_integer_median(open_wages)),
                "mean_wage": float(sum(open_wages) // max(1, len(open_wages))),
                "credit_outstanding_cents": float(outstanding),
                "default_rate": float(defaults * 10_000 * year // max(1, current_at_start * week)),
                "lending_rate_bp": float(lending_rate),
            }
        )
        if credit_year_ago_cents is not None and credit_year_ago_cents > 0:
            values["credit_growth_yoy"] = float(
                10_000 * (outstanding - credit_year_ago_cents) // credit_year_ago_cents
            )

    if _due(tick, quarter):
        quarter_start = max(1, tick - quarter + 1)
        consumption, investment, government, intermediates, revenues = _transaction_flows(
            economy,
            population_ids,
            from_tick=quarter_start,
            to_tick=tick,
        )
        inventory = _inventory_value_cents(economy)
        inventory_before = (
            previous_inventory_cents
            if previous_inventory_cents is not None
            else economy.initial_inventory_value_cents
        )
        inventory_change = inventory - inventory_before
        nominal = consumption + investment + government + inventory_change
        production = sum(revenues.values()) - intermediates
        cpi_ticks = [row_tick for row_tick in economy.cpi_history_bp if row_tick <= tick]
        current_cpi = economy.cpi_history_bp[max(cpi_ticks)] if cpi_ticks else 10_000
        adults = [agent for agent in population if agent.alive and agent.age_years >= 18]
        wealth = [economy.ledger.net_worth(agent.agent_id) for agent in adults]
        income = _income_by_agent(
            economy.gross_income_by_tick,
            from_tick=max(1, tick - year + 1),
            to_tick=tick,
        )
        income_values = [income.get(agent.agent_id, 0) for agent in adults]
        wages = sum(
            sum(rows.values())
            for row_tick, rows in economy.gross_wages_by_tick.items()
            if quarter_start <= row_tick <= tick
        )
        hhi = _hhi_sector_bp(economy, revenues)
        values.update(
            {
                "gdp_nominal": float(nominal),
                "gdp_production": float(production),
                "gdp_real": float(nominal * 10_000 // max(1, current_cpi)),
                "share_negative_networth": float(
                    10_000 * sum(value < 0 for value in wealth) // max(1, len(wealth))
                ),
                "wealth_share_undefined": float(sum(wealth) <= 0),
                "inventory_value_cents": float(inventory),
                "term_spread_bp": float(
                    (
                        sum(loan.outstanding_cents * loan.annual_rate_bp for loan in live_loans)
                        // outstanding
                    )
                    - economy.policy_rate_bp
                    if outstanding
                    else 0
                ),
            }
        )
        wealth_gini = _gini_bp(wealth)
        if wealth_gini is not None:
            values["gini_wealth"] = float(wealth_gini)
        income_gini = _gini_bp(income_values)
        if income_gini is not None:
            values["gini_income"] = float(income_gini)
        if nominal > 0:
            values["labour_share"] = float(10_000 * wages // nominal)
        shares = _wealth_shares_bp(wealth)
        if shares is not None:
            values.update({key: float(value) for key, value in shares.items()})
        if hhi is not None:
            values["hhi_sector"] = float(hhi)
        cpi_year_ago = economy.cpi_history_bp.get(tick - year)
        if cpi_year_ago is not None:
            values["inflation_yoy"] = float(10_000 * current_cpi // max(1, cpi_year_ago) - 10_000)
        if tick >= year and inventory_year_ago_cents is not None:
            year_start = tick - year + 1
            c_y, i_y, g_y, _m_y, _revenues_y = _transaction_flows(
                economy,
                population_ids,
                from_tick=year_start,
                to_tick=tick,
            )
            gdp_ttm = c_y + i_y + g_y + inventory - inventory_year_ago_cents
            if gdp_ttm > 0:
                values["credit_to_gdp_bp"] = float(10_000 * outstanding // gdp_ttm)
                if values["m1"] > 0:
                    values["velocity"] = float(10_000 * gdp_ttm // int(values["m1"]))
    return values

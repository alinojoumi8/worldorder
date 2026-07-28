# Policy runtime handback

`RuntimeConfig` is the single policy overlay. `LayeredOverlay` remains its compatibility
name; there is no second polity-owned cache. C18 appends enactments through
`RuntimeConfig.enact`, checkpoints them through `name`/`dump`/`load`, and reconstructs them
from `POLICY_ENACTED` (`12030`) with `project_enactment`. An enactment always takes effect
after its event tick.

The ledger boundary is `PolityLedger`. Production wiring uses
`EconomyLedgerAdapter`, which obtains canonical accounts from the economy ledger and posts
balanced transfers. Polity code does not import `polis.economy`. The configured treasury
owner is passed into party and election services as `treasury_id`; application composition
must use the canonical `gv_treasury` owner supplied by C11.

## Required live reads

Consumers must read these keys at the tick of use. Rates use `runtime.bp`, money uses
`runtime.cents`, flags use `runtime.flag`, brackets use `runtime.brackets`, and enum/count
values use `runtime.get`.

| Runtime key | Effect site / owner |
|---|---|
| `tax.income.brackets` | Payroll withholding (C11) |
| `tax.corporate_bp` | Firm fiscal close (C11) |
| `tax.capital_gains_bp` | Exchange settlement (C12) |
| `tax.inheritance_bp` | Estate settlement (C14) |
| `tax.vat_bp` | Goods purchase settlement (C11) |
| `money.policy_rate_bp` | Central-bank policy transmission (C11) |
| `welfare.unemployment_benefit_cents` | Unemployment transfer (C14) |
| `welfare.benefit_duration_ticks` | Benefit eligibility (C14) |
| `welfare.pension_cents` | Pension transfer (C14) |
| `welfare.child_benefit_cents` | Child-benefit transfer (C14) |
| `education.spend_cents_per_student` | School budget allocation (C21) |
| `education.compulsory_until_age` | Enrolment eligibility (C21) |
| `police.budget_cents` | Police staffing and operations (C19) |
| `courts.budget_cents` | Court capacity (C19) |
| `courts.loser_pays` | Civil judgment costs (C19) |
| `prison.capacity` | Custody admission (C19) |
| `sentencing.multiplier_bp` | Sentence calculation (C19) |
| `labour.minimum_wage_cents` | Vacancy and offer floors (C11) |
| `labour.max_hours_per_sim_week` | Work scheduling (C11) |
| `regulation.finance.margin_allowed` | Margin-order gate (C12) |
| `regulation.finance.short_selling_allowed` | Short-order gate (C12) |
| `regulation.finance.insider_trading_enforced` | Market enforcement (C19) |
| `regulation.labour.at_will_dismissal` | Dismissal gate (C11) |
| `regulation.media.disclosure_required` | Publication disclosure (C17) |
| `regulation.housing.rent_cap_bp` | Rent setting (C14) |
| `migration.quota_per_sim_year` | Migration admission (C14) |
| `polity.campaign_cap_cents` | Campaign resource gate (C18) |
| `polity.felon_franchise` | Election eligibility (C18/C19) |
| `government.debt_ceiling_cents` | Fiscal admissibility (C18) |
| `society.feed_algorithm` | Feed ranking (C16; enabled only by `can_regulate_feed`) |
| `government.public_notices_budget_cents` | Public-notice distribution (C17) |

The society specification lists 30 policy rows while C18 requires 31 and requires the C17
public-notice budget. The closed registry therefore includes
`government.public_notices_budget_cents` as the reconciled thirty-first row.

## Coordination record

- Election-day budget: the deliberate subset is supplied by the existing mandatory-action
  path; `ElectionOffice.hold` reports deliberate and reflex counts and reruns an unusable fit
  one simulated week later.
- Overlay shape: the existing `RuntimeConfig`/`LayeredOverlay` implementation owns both
  typed reads and append-only writes.
- Checkpoints: `RuntimeConfig` is checkpointable and the C18 round-trip test registers it
  directly with `CheckpointManager`.
- Government ledger: composition supplies the treasury owner/account identity; polity
  transfers remain behind the ledger adapter.

No scientific calibration metric is inferred from the unit fixtures. The three-sim-year
policy metrics belong to the C24b measurement/export run and must be reported only from an
actual integrated run with its reproducibility tuple.

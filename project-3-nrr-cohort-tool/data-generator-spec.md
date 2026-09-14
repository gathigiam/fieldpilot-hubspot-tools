# FieldPilot Synthetic Data Generator — Design Spec (Project #3)

Populates the FieldPilot123 HubSpot test portal with realistic multi-month
cohort history via the API, so the cohort_nrr Postgres table has real data
to compute against. Runs via the "FieldPilot Scripts" Service Key (write
scopes on Companies/Deals) -- NOT via the read-only Claude/HubSpot MCP
connector, which is intentionally view-only.

This is throwaway/seed-script territory per your usual convention -- not
meant to be committed to git as a reusable tool, just run once (or re-run
against a wiped portal) to produce a believable dataset.

---

## BLOCKING ACTION -- do this before running the generator

`deal_type` currently has 4 live values. Two more need adding in the
HubSpot UI before this script can create a single deal:
- **Reactivation** (already decided, not yet added)
- **Churn** (decided this session -- covers full cancellation, distinct
  from Downgrade, which still means "still a customer, paying less")

## deal_type -> formula bucket mapping (all 6 values)

| deal_type     | Formula bucket                          |
|---------------|------------------------------------------|
| New Business  | starting_mrr (month 0 baseline)           |
| Expansion     | expansion_mrr                             |
| Downgrade     | contraction_mrr                           |
| Churn         | churn_mrr                                 |
| Reactivation  | reactivation_mrr                          |
| Renewal       | none -- $0 MRR impact, but still generated (see below) |

## Segmentation & renewal cadence

`plan_tier` (Solo / Small Crew / Multi-Crew / Enterprise) already exists
and drives cadence directly -- no new threshold invented:

- **Solo, Small Crew** -> monthly renewal cadence (a Renewal-type decision
  point every month)
- **Multi-Crew, Enterprise** -> annual renewal cadence (one decision point
  per year, on the signup anniversary)

## Why Renewal deals still get generated even though they're $0 MRR impact

Renewal is the actual **decision point** in the simulation -- this is
where the generator rolls what happens to an account next (flat / expand
/ downgrade / churn), mirroring how real accounts behave: expansion and
churn cluster around contract renewal, not randomly across the calendar.
A quiet month between renewals really is quiet. Tracking renewal count
per company (trivially derivable later from raw deal history -- no new
column needed) is also a plausible future signal worth having in the
data, per the idea that renewal-count patterns might predict what happens
next.

## Time span & cohort composition (defaults -- adjust freely)

- **18 months of cohort history** -- gives a mix of mature cohorts (many
  renewals behind them) and brand-new ones (just seeded, elapsed_month 0
  only).
- **~10 new companies per month**, tier mix weighted toward smaller
  accounts (matches the real market's shape, where SMB tools serve a much
  larger base than enterprise-tier ones):
  - Solo: 35%
  - Small Crew: 35%
  - Multi-Crew: 20%
  - Enterprise: 10%

## Renewal decision-point probabilities (defaults -- adjust freely)

Loosely anchored to real industry figures found this session (35-45% of
renewals including expansion; mid-single-digit-to-low-teens churn
depending on performance tier) -- NOT precise measured facts, just a
believable starting point.

**Annual cadence (Multi-Crew, Enterprise) -- per renewal event:**
- Flat renewal: 45%
- Expansion: 35%
- Downgrade: 10%
- Churn: 10%

**Monthly cadence (Solo, Small Crew) -- per renewal event, deliberately
scaled down so 12 events/year doesn't compound into an absurd annual
churn/expansion rate:**
- Flat renewal: 92%
- Expansion: 4%
- Downgrade: 2.5%
- Churn: 1.5%

## Same-month deal folding

Any deal dated in the same calendar month as a company's `customer_since`
gets folded into `starting_mrr` rather than generated as a separate
Expansion deal at elapsed_month 0 (decided earlier this session).

## Reactivation modeling (defaults -- adjust freely)

Of companies that churn:
- **20%** reactivate within the 6-month grace window -- same cohort kept,
  `customer_since` unchanged, deal_type = Reactivation.
- **10%** reactivate after the grace window has passed -- treated as a
  brand-new signup: fresh `customer_since`, new cohort, deal_type = New
  Business.
- **70%** never come back.

## What the script actually creates, per company

1. One Company record: `customer_since`, `plan_tier`, `crew_size`,
   `monthly_recurring_revenue` (running total, kept in sync as deals post).
2. One New Business deal at signup (`mrr_amount` = starting MRR).
3. One Renewal-type decision point per cadence period thereafter, each
   producing either nothing further (flat) or an Expansion / Downgrade /
   Churn deal per the probabilities above, until either the simulation's
   present-day cutoff is reached or the company churns.
4. If churned: a probability roll for reactivation timing, per above.

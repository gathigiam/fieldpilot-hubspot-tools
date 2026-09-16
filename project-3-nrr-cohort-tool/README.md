# FieldPilot NRR & Cohort Analytics Engine

Part of the FieldPilot portfolio (a fictional HVAC/plumbing/electrical
field-service SaaS) — a two-part pipeline that seeds realistic multi-month
customer cohort history into HubSpot, then computes true cumulative Net
Revenue Retention (NRR) and Gross Revenue Retention (GRR) per cohort, per
month, and writes the results to Postgres.

## The problem this solves

HubSpot's native reporting cannot group customers into acquisition cohorts
and track their revenue movement — expansion, contraction, churn — across
subsequent periods. That's exactly what a cohort NRR calculation requires,
and it's a real, recognized gap (not just assumed — verified via research
early in this build, corroborated across multiple sources, with the caveat
that most sources making the claim also sell tools that solve it).

## What it does

**`generate_synthetic_data.py`** — seeds ~180 companies and ~1,150+ deals
of realistic 18-month cohort history into a HubSpot test portal via the
batch API. A real NRR demo needs staggered acquisition months plus
follow-on expansion/downgrade/churn/reactivation deals over time — building
that by hand in the HubSpot UI wasn't practical, so this generates it
programmatically instead. Renewal points (not random calendar months) are
the actual decision points where an account's fate gets decided, mirroring
how real subscription businesses behave — expansion and churn genuinely
cluster around contract renewals, not randomly across the year.

**`compute_cohort_nrr.py`** — reads every Company and Deal in the portal,
groups companies into cohorts by signup month, walks each cohort forward
computing cumulative (not just that-month) revenue movement, and does a
full transactional rebuild of a `cohort_nrr` Postgres table. This table is
the actual deliverable — the generator is setup work to give it something
real to compute against.

## Architecture

```
HubSpot (Companies + Deals — source of truth)
        │
        ▼
compute_cohort_nrr.py  (Python, psycopg2, closedate-driven)
        │
        ▼
Postgres `cohort_nrr`  (Docker, isolated container on a shared VPS)
        │
        ▼
(reporting dashboard — planned, separate project)
```

## Design decisions worth understanding, not just using

- **Fixed cohort-anchor denominator, not rolling.** Every row for a cohort
  divides by that cohort's *original* starting MRR, forever — not last
  month's MRR. A rolling denominator would let a slow multi-year decline
  look fine every single month, since each month would just measure
  against wherever it already landed.
- **Cumulative math, not period-by-period.** A bad month doesn't
  un-happen just because a later month partially recovers. Each row tracks
  running totals from the cohort's month 0 forward, not just that month's
  isolated flow.
- **GRR is clamped to a 0.00 floor, deliberately.** Real GRR is bounded
  0-100% by definition. The raw formula can go out-of-domain (negative)
  for a company that expanded heavily before fully churning, because churn
  reflects current (possibly-expanded) MRR while GRR's formula never adds
  expansion back in. The floor is applied only to the derived percentage —
  the underlying dollar columns (`cumulative_contraction_mrr`,
  `cumulative_churn_mrr`) are left exactly as computed, since those are
  true figures a dashboard may still want.
- **Renewal cadence varies by customer segment**, mirroring the real
  field-service SaaS market: smaller accounts (Solo/Small Crew) get
  monthly cadence, matching how Jobber/Housecall Pro actually sell;
  larger accounts (Multi-Crew/Enterprise) get annual cadence, matching
  ServiceTitan's real contract terms.
- **A churned account reactivating within 6 months returns to its
  original cohort; past 6 months, it's treated as an entirely new
  signup** (fresh cohort, `New Business` not `Reactivation`). This makes
  the "when does a cohort's row-generation stop" question provable rather
  than heuristic: since any late win-back always spawns a new cohort by
  design, a cohort dormant 6 straight months genuinely cannot revive.
- **Full table rebuild every run, not incremental upsert** — the right
  call at this scale (a few hundred companies), and deliberately not the
  right call at real production scale, where a full rebuild's cost grows
  with total history rather than what actually changed. Documented as a
  known tradeoff, not a limitation nobody noticed.

## A real bug this build found and fixed

HubSpot's `batch/create` endpoint does **not** guarantee its response
order matches the request order. The generator's original logic linked
each newly-created HubSpot ID back to its in-memory record by matching
list position (`zip(batch, response["results"])`) — which silently
mis-attributed some deals to the wrong companies whenever HubSpot happened
to reorder a batch response.

This was caught by chasing a division-by-zero error four layers back to
its root cause: a company whose deal history included dates *before its
own signup date* — structurally impossible unless deals were attached to
the wrong company. Fixed by matching each created record back to its
request by a unique field already present on every payload (company
name / deal name), instead of trusting response order. The fix was
verified against a **deliberately shuffled fake response**, not just the
one live run that happened to look correct — since a bug like this can
pass silently on an in-order response and only surface probabilistically.

## Honest status of the script's defensive paths

Some guard logic (skip a cohort with $0 starting MRR, skip a deal with an
unrecognized `deal_type`, floor GRR at 0%) is verified two different ways,
and it's worth being precise about which:

| Guard | Verified via test | Verified against real live data |
|---|---|---|
| Unrecognized/null `deal_type` skip | Yes | Yes (one real legacy deal hit this) |
| Missing/malformed `mrr_amount` default-to-0 | Yes | Not yet — no live deal has hit this |
| No-company / ambiguous-association skip | Yes | Not yet — 0 occurrences live so far |
| Zero-starting-MRR cohort skip | Yes | Not yet — the only cohorts that would have hit it belonged to a corrupted run, since archived |
| GRR 0.00 floor | Yes | Not yet — 0 rows have needed it in any clean run so far |

None of this means anything is broken — it means the current dataset
hasn't produced the conditions that would exercise these paths. That's a
description of the data, not a weakness in the script.

## Setup

**HubSpot custom properties required** (Companies unless noted):
`customer_since` (Date), `monthly_recurring_revenue` (Number),
`subscription_status` (Dropdown: Active/Churned), `churned_date` (Date),
`synthetic_run_id` (Text, on both Companies and Deals), and on Deals:
`deal_type` (Dropdown: New Business/Expansion/Downgrade/Renewal/
Reactivation/Churn), `mrr_amount` (Number), `crew_size_requested` (Number,
shared with the handoff-automation tool).

**Postgres**: runs in its own isolated Docker container (see
`docker-compose.yml`), separate from any other service sharing the same
VPS. Copy `.env.example` to `.env` and fill in real credentials — never
commit the real `.env`.

**Run order**:
```
py generate_synthetic_data.py --dry-run --dry-run-output preview.json  # inspect first
py generate_synthetic_data.py                                          # live write
py compute_cohort_nrr.py                                               # reads HubSpot, writes cohort_nrr
```

## Tech stack

Python, HubSpot CRM API (batch create/read, Search API, custom
properties), PostgreSQL (Docker), `psycopg2`, `Decimal`-based arithmetic
throughout to avoid floating-point drift across cumulative sums.

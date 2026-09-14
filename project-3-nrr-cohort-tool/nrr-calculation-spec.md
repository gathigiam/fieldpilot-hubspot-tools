# FieldPilot NRR/Cohort Calculation Script — Design Spec (Project #3, Part 2)

Reads Company + Deal records from the FieldPilot123 HubSpot portal, computes
per-cohort NRR/GRR retention curves, and does a full transactional rebuild of
the `cohort_nrr` Postgres table. This is the actual core deliverable of
Project #3 -- the data generator was setup work to give this script
something real to compute against.

---

## Verified against the live portal before writing this spec (not assumed)

- **Use `closedate`, not `createdate`, for every date-based grouping.**
  `createdate` clusters on whatever day the generator actually ran (useless
  for cohort math). `closedate` was confirmed across a real sample of 9
  deals spanning January-September 2026 -- genuine historical variety,
  matching the simulated timeline.
- **Cross-checked count**: 40 total churns this run, 8 in-window
  reactivations, live search for currently-Churned companies returned
  exactly 32 (40 - 8) -- the independent numbers reconcile.

## Scope

Process the **entire portal's** Company and Deal data -- do NOT filter by
`synthetic_run_id`. That property is generator-internal bookkeeping only
and must never be read by this calculation logic (same rule as the
company-to-company win-back association metadata).

## Core algorithm

1. Pull every Company (`customer_since`) and every Deal (`deal_type`,
   `mrr_amount`, `closedate`, plus which company it belongs to) -- paginate
   through all of both; ~1,200+ deals will exceed a single page.

   **RESOLVED (confirmed against HubSpot docs, then live-tested against a
   real deal in this portal -- not assumed):** `GET /crm/v3/objects/deals`
   (the plain list endpoint) supports an `associations=companies` query
   parameter that returns each deal's associated company ID **inline**,
   e.g. `?associations=companies&properties=deal_type,mrr_amount,closedate`.
   No separate batch-associations read call is needed -- this folds into
   the same paginated deal-pull (`limit` + `paging.next.after` cursor),
   one round-trip per page of up to 100 deals. **Not available on the
   Search endpoint** (`/crm/v3/objects/deals/search` has no `associations`
   parameter) -- moot here since this script has no property filter to
   search on anyway; it needs every deal in the portal, which is exactly
   what the list endpoint is for.

   **Open question resolved by construction, not by confirming the fact
   (evidence-quality gap, noted honestly rather than papered over):** the
   Search endpoint's "archived records are categorically unreachable, no
   override exists" was confirmed directly against HubSpot's own
   community/Ideas threads discussing that exact endpoint. The List
   endpoint used here is a different endpoint with a different (undocumented)
   default -- official reference pages for both `/crm/v3/objects/deals` and
   `/crm/v3/objects/companies` don't list an `archived` parameter at all in
   their parameter tables, and the community threads specifically about
   deals list-endpoint archived behavior were login-walled and couldn't be
   opened to confirm firsthand. Rather than trust an unconfirmed default
   either way, **both list calls pass `archived=false` explicitly** --
   correct by construction regardless of what HubSpot's actual default
   turns out to be, and costs nothing to add. Matters starting the moment
   `cleanup_synthetic_data.py` is used on a future re-run -- a wrong
   assumption here would let an old, archived run's records leak back into
   the calculation.

   **Live-tested response shape** (confirmed via a real deal in
   FieldPilot123, not just the docs example): each deal's
   `associations.companies.results` array contains **two entries for the
   same company** -- one `"type": "deal_to_company"`, one
   `"type": "deal_to_company_unlabeled"` -- both carrying the identical
   company ID. Dedup (e.g. take `results[0].id`, or collapse to a set) is
   needed; don't assume one result per company.

   **Two live-data findings from this same check, not yet addressed
   anywhere in this spec:**
   - A non-synthetic deal already exists in the portal with
     `deal_type: null` (predates/falls outside this project's generator
     runs -- consistent with "process the entire portal," but the
     deal_type -> bucket mapping in step 4 has no entry for `null` or any
     other unrecognized value).
   - HubSpot returns custom property values as strings regardless of
     underlying type (`"mrr_amount": "500"`, not `500`) -- and `null` when
     unset, seen on the same deal above. The calculation needs explicit
     casting with null-handling before any arithmetic, not an assumed
     numeric type.

   **RESOLVED -- per-deal validation policy (decided this session):**
   Loud-but-non-fatal for both problems below; a stray legacy record from
   another project sharing this portal must never fail the whole run.

   - **deal_type checked first, strictly before any mrr_amount casting** --
     a skipped deal's null/malformed mrr_amount must never reach the cast.
     If deal_type is null or not one of the 6 recognized values: skip the
     deal entirely (it contributes to no bucket), but increment a counter
     per distinct bad value seen and log it, so the skip is visible in the
     run's output, not silently swallowed.
   - **mrr_amount checked second, only for deals that passed the deal_type
     check.** If a recognized-deal_type deal has a missing or malformed
     mrr_amount (fails the numeric cast): treat its value as 0, log a
     warning naming the deal ID and the bad value, and continue -- same
     loud-but-non-fatal pattern as the deal_type skip, just scoped to one
     deal's contribution instead of the whole run.
   - Both cases print/log a final summary count (e.g. "N deals skipped for
     unrecognized deal_type: {value: count, ...}", "M deals defaulted to
     mrr_amount=0") so a run with data-quality issues is never
     indistinguishable from a clean one.

   **RESOLVED -- same pattern extended to the cohort level (found live,
   2026-09-14): a cohort with starting_mrr == 0** (no New Business deal
   landed at elapsed_month 0) is skipped entirely -- no rows generated for
   it -- rather than crashing on the nrr_pct/grr_pct division. This isn't a
   new special case, it's the identical loud-but-non-fatal shape as the
   per-deal checks above, just one level up: a stray legacy
   company/cohort from another project sharing this portal must never fail
   the whole run. Safe specifically because no legitimate synthetic cohort
   can ever have starting_mrr == 0 -- the generator's crew_size floor (1)
   and fixed positive PRICE_PER_TECHNICIAN mean a real cohort can never
   hit this path, so it only ever catches genuine legacy noise, never a
   real cohort silently going missing. The skip is counted and logged
   (cohort_month + how many companies it represents) in the same final
   summary as the deal-level counts, for the same reason: a run touching
   messy data must never look identical to a clean run.

   First surfaced live: this portal has 7 such cohorts predating this
   project's use of it, one of them (`2026-01-01`) apparently reconstructed
   entirely from `Renewal`/`Churn` deals with no `New Business` at all --
   consistent with the portal being shared across projects rather than a
   bug in the fetch/grouping logic (confirmed by checking one such
   company's deal history directly against the live API).
2. Group companies by `cohort_month` = first-of-month truncation of
   `customer_since`.
3. Per cohort: `starting_mrr` = sum of New Business deal `mrr_amount`
   across every company in that cohort. (Same-month follow-on deals need
   no special folding logic here -- the generator already bakes them into
   the New Business deal itself, so New Business is the only deal_type
   that should ever appear at elapsed_month 0.)
4. deal_type -> bucket mapping (fixed):
   - New Business -> starting_mrr (month 0 only)
   - Expansion -> expansion_mrr
   - Downgrade -> contraction_mrr
   - Churn -> churn_mrr
   - Reactivation -> reactivation_mrr
   - Renewal -> no bucket, $0 impact, excluded from the calculation
     entirely
5. For each deal: elapsed_month = (deal's closedate year/month) minus
   (company's cohort_month year/month), as a whole month count.
6. Walk each cohort forward from elapsed_month 0: track both **period**
   totals (that month's flow only, for the "why did it move" story) and
   **cumulative** totals (running sum from elapsed_month 0 through the
   current row -- this is what the formula actually uses, per the
   period-vs-cumulative correction made earlier this session).
7. Derive per row:
   - `current_mrr` = starting_mrr + cumulative_expansion + 
     cumulative_reactivation - cumulative_contraction - cumulative_churn
   - `nrr_pct` = current_mrr / starting_mrr x 100
   - `grr_pct` = (starting_mrr - cumulative_contraction - 
     cumulative_churn) / starting_mrr x 100
   - Both fixed at 100.00 at elapsed_month 0.

   **RESOLVED -- grr_pct clamped to a 0.00 floor (found via implementation
   testing, not anticipated when this spec was first written):** GRR is
   bounded 0-100% by definition in real accounting, but the formula above
   can go out of domain -- `churn_mrr` always reflects a company's actual
   MRR at the moment it churns (which can already include prior expansion,
   per the generator's own churn logic: it churns for the *current*
   running MRR, not the original `starting_mrr`), while the GRR formula
   compares against `starting_mrr` alone and never adds expansion back in.
   A company that expanded a lot before fully churning can therefore show
   `cumulative_contraction + cumulative_churn > starting_mrr`, producing a
   negative grr_pct. Confirmed via a hand-traced example
   (starting_mrr=500, +200 expansion, then churns for the full current 600
   -> grr_pct = -40.00 unclamped). The derived **percentage** is clamped
   to a 0.00 floor before being written; the underlying
   `cumulative_contraction_mrr`/`cumulative_churn_mrr` dollar columns are
   left exactly as computed, un-clamped, since those figures are accurate
   and Project #4 may want them as-is.

   `nrr_pct` has no equivalent issue and is never clamped: bounded below
   by construction, since a Churn deal's `mrr_amount` is always <= the
   running MRR it's subtracted from (the generator churns for "whatever's
   left," never more), which drives `current_mrr` to exactly 0 on churn,
   never negative. Confirmed both algebraically and empirically (0
   negative occurrences across a full simulated 180-company run).
8. Stop generating rows for a cohort at whichever comes first:
   - `calendar_month` would exceed today's real month, or
   - `current_mrr` has been exactly 0 for 6 consecutive elapsed_months
     (provably permanent under this generator's design -- any reactivation
     past the grace window always spawns a new company/cohort instead, so
     a cohort dormant this long truly cannot revive).

## Write strategy: full rebuild, inside one transaction

Not incremental/upsert -- at this scale (180 companies, ~1,200 deals),
recomputing everything from scratch every run is simpler and cheap, and
avoids the harder problem incremental updates would create here: because
nrr_pct/grr_pct are cumulative, a single backdated deal would require
recomputing every subsequent row for that cohort, not just one.

Critical: wrap the full DELETE + re-INSERT in a single Postgres
transaction. If the run fails partway through, the whole thing rolls back
and the table is left exactly as it was before -- never caught half-empty,
which matters once Project #4's dashboard is reading from this table live.

(Note for future reference, not a decision needed now: at real production
scale this pattern breaks down -- full rebuild cost scales with total
history, not what changed. The fix there is incremental at the cohort
level: track which companies had new/changed deals, recompute only the
cohorts those companies belong to from scratch, skip everything untouched.
Not needed here, but worth knowing why it's not needed here.)

## Schema reminder (already built, from cohort_nrr_schema.sql)

`current_mrr`, `nrr_pct`, and `grr_pct` have no column default -- Postgres
itself will reject a row that doesn't explicitly supply all three, which
is a real safety net against a silent gap in the write logic, not just a
formality.

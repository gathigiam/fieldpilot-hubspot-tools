"""
FieldPilot NRR/Cohort Calculation Script (Project #3, Part 2).

Reads every Company + Deal record from the FieldPilot123 HubSpot portal,
computes per-cohort NRR/GRR retention curves, and does a full transactional
rebuild of the cohort_nrr Postgres table. See nrr-calculation-spec.md for
the full design rationale -- this is the actual core deliverable of
Project #3; the synthetic data generator was setup work to give this
script something real to compute against.

Processes the ENTIRE portal's Company and Deal data -- does NOT filter by
synthetic_run_id. That property is generator-internal bookkeeping only and
must never be read by this calculation logic (same rule as the
company-to-company win-back association metadata).

Runs via the "FieldPilot Scripts" Service Key (HUBSPOT_TOKEN in .env) and
connects to Postgres via DATABASE_URL in .env (through an SSH tunnel to the
VPS in dev -- see project memory for the tunnel command).
"""

import calendar
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ.get("HUBSPOT_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}
BASE_URL = "https://api.hubapi.com"
PAGE_SIZE = 100

TODAY = date.today()

# deal_type -> bucket, fixed mechanics per spec step 4. "None" buckets
# (Renewal) have $0 impact and are excluded from the calculation entirely.
DEAL_TYPE_BUCKET = {
    "New Business": "starting_mrr",
    "Expansion": "expansion_mrr",
    "Downgrade": "contraction_mrr",
    "Churn": "churn_mrr",
    "Reactivation": "reactivation_mrr",
    "Renewal": None,
}

DORMANCY_MONTHS = 6  # consecutive current_mrr == 0 months that stop a cohort's rows


# --------------------------------------------------------------------------
# Fetch phase -- HubSpot, read-only, paginated
# --------------------------------------------------------------------------

def _paginate(object_type, properties, associations=None):
    """Yields every non-archived record of object_type, paginating via
    limit + paging.next.after. archived=false is passed explicitly rather
    than relied on as an unconfirmed default -- see nrr-calculation-spec.md
    step 1's note on the List-vs-Search archived-default evidence gap."""
    params = {
        "limit": PAGE_SIZE,
        "properties": ",".join(properties),
        "archived": "false",
    }
    if associations:
        params["associations"] = ",".join(associations)

    after = None
    while True:
        page_params = dict(params)
        if after:
            page_params["after"] = after

        resp = requests.get(
            f"{BASE_URL}/crm/v3/objects/{object_type}", headers=HEADERS, params=page_params
        )
        if resp.status_code != 200:
            raise SystemExit(
                f"Failed to fetch {object_type} (HTTP {resp.status_code}): {resp.text}"
            )
        body = resp.json()
        yield from body.get("results", [])

        paging = body.get("paging", {}).get("next")
        if not paging:
            break
        after = paging["after"]


def fetch_companies():
    """Returns {company_id: customer_since (date) or None}."""
    companies = {}
    for record in _paginate("companies", properties=["customer_since"]):
        raw = record["properties"].get("customer_since")
        companies[record["id"]] = _parse_hubspot_date(raw) if raw else None
    return companies


def fetch_deals():
    """Returns a list of raw deal dicts: {id, deal_type, mrr_amount_raw,
    closedate_raw, company_id}. company_id is None if the deal has no
    company association (or more than one distinct one, which shouldn't
    happen but isn't assumed away -- see dedup below)."""
    raw_deals = []
    for record in _paginate(
        "deals",
        properties=["deal_type", "mrr_amount", "closedate"],
        associations=["companies"],
    ):
        company_results = (
            record.get("associations", {}).get("companies", {}).get("results", [])
        )
        # Each association appears twice -- once labeled "deal_to_company",
        # once "deal_to_company_unlabeled" -- both pointing at the same
        # company ID (confirmed live against a real deal, see spec). Dedup
        # to the actual set of distinct company IDs rather than assuming
        # exactly one result.
        distinct_company_ids = {r["id"] for r in company_results}
        company_id = next(iter(distinct_company_ids)) if len(distinct_company_ids) == 1 else None

        props = record["properties"]
        raw_deals.append({
            "id": record["id"],
            "deal_type": props.get("deal_type"),
            "mrr_amount_raw": props.get("mrr_amount"),
            "closedate_raw": props.get("closedate"),
            "company_id": company_id,
            "distinct_company_id_count": len(distinct_company_ids),
        })
    return raw_deals


def _parse_hubspot_date(raw):
    """HubSpot date/datetime properties come back as ISO strings, either a
    bare date (customer_since-style) or a full timestamp with a time
    component (closedate-style). Always returns a date."""
    if raw is None:
        return None
    # Full timestamp: "2026-09-12T19:54:01.698Z" -- take the date part.
    date_part = raw.split("T")[0]
    return date.fromisoformat(date_part)


# --------------------------------------------------------------------------
# Validation phase
# --------------------------------------------------------------------------

def validate_deals(raw_deals):
    """Splits raw_deals into valid deals (ready for grouping) and drops the
    rest, per the loud-but-non-fatal policy: deal_type checked strictly
    before any mrr_amount casting, so a skipped deal's null/malformed
    mrr_amount never reaches the cast. Returns (valid_deals, counters)."""
    valid_deals = []
    skipped_deal_type_counts = Counter()
    defaulted_mrr_amount = []  # list of (deal_id, raw_value) for the warning log
    skipped_no_company = 0

    for d in raw_deals:
        deal_type = d["deal_type"]
        if deal_type not in DEAL_TYPE_BUCKET:
            skipped_deal_type_counts[deal_type] += 1
            continue  # mrr_amount is never touched for a skipped deal

        if d["company_id"] is None:
            # No association, or an ambiguous >1 distinct company -- either
            # way this deal can't be grouped into a cohort. Not the same
            # failure mode as a bad deal_type/mrr_amount, so tracked
            # separately rather than folded into those counters.
            skipped_no_company += 1
            continue

        closedate = _parse_hubspot_date(d["closedate_raw"])
        if closedate is None:
            skipped_deal_type_counts["<deal_type ok but closedate missing>"] += 1
            continue

        mrr_amount = _cast_mrr_amount(d["mrr_amount_raw"])
        if mrr_amount is None:
            defaulted_mrr_amount.append((d["id"], d["mrr_amount_raw"]))
            mrr_amount = Decimal("0")

        valid_deals.append({
            "company_id": d["company_id"],
            "deal_type": deal_type,
            "bucket": DEAL_TYPE_BUCKET[deal_type],
            "mrr_amount": mrr_amount,
            "closedate": closedate,
        })

    counters = {
        "skipped_deal_type_counts": skipped_deal_type_counts,
        "defaulted_mrr_amount": defaulted_mrr_amount,
        "skipped_no_company": skipped_no_company,
    }
    return valid_deals, counters


def _cast_mrr_amount(raw):
    """Returns a Decimal, or None if raw is missing/malformed (caller
    defaults to Decimal('0') and logs a warning -- this function only
    detects the failure, doesn't apply the fallback, so the two concerns
    stay separate)."""
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def print_validation_summary(counters, total_raw_deals, total_valid_deals):
    skipped_deal_type_counts = counters["skipped_deal_type_counts"]
    defaulted_mrr_amount = counters["defaulted_mrr_amount"]
    skipped_no_company = counters["skipped_no_company"]

    total_skipped_deal_type = sum(skipped_deal_type_counts.values())
    print(
        f"\nValidation: {total_valid_deals}/{total_raw_deals} deals usable."
    )
    print(
        f"  Skipped for unrecognized/missing deal_type: {total_skipped_deal_type} "
        f"{dict(skipped_deal_type_counts) if skipped_deal_type_counts else '{}'}"
    )
    print(f"  Skipped for no/ambiguous company association: {skipped_no_company}")
    print(f"  Defaulted mrr_amount to 0 (missing/malformed): {len(defaulted_mrr_amount)}")
    for deal_id, raw_value in defaulted_mrr_amount:
        print(f"    deal {deal_id}: mrr_amount={raw_value!r}")


# --------------------------------------------------------------------------
# Grouping phase
# --------------------------------------------------------------------------

def add_months(d, months):
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def whole_month_diff(later, earlier):
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def group_by_cohort(companies, valid_deals):
    """Returns {cohort_month: {"deals_by_elapsed_month": {elapsed_month: [deal, ...]}}}.
    Companies with no customer_since are excluded -- they can't be assigned
    a cohort at all."""
    cohort_month_of_company = {}
    for company_id, customer_since in companies.items():
        if customer_since is not None:
            cohort_month_of_company[company_id] = customer_since.replace(day=1)

    cohorts = defaultdict(lambda: defaultdict(list))
    skipped_no_cohort = 0

    for deal in valid_deals:
        cohort_month = cohort_month_of_company.get(deal["company_id"])
        if cohort_month is None:
            skipped_no_cohort += 1
            continue
        elapsed_month = whole_month_diff(deal["closedate"], cohort_month)
        cohorts[cohort_month][elapsed_month].append(deal)

    return cohorts, skipped_no_cohort


# --------------------------------------------------------------------------
# Cohort walk phase
# --------------------------------------------------------------------------

def compute_cohort_rows(cohort_month, deals_by_elapsed_month):
    """Walks one cohort forward from elapsed_month 0, returning a list of
    row dicts, or None if the cohort has to be skipped entirely.

    Same loud-but-non-fatal pattern as validate_deals()'s per-deal checks,
    just applied at the cohort level instead of the deal level: a cohort
    with starting_mrr == 0 (no New Business deal landed at elapsed_month 0
    -- e.g. a legacy company from another project sharing this portal,
    with no New Business deal at all, or one that got filtered out
    upstream) can't produce a meaningful nrr_pct/grr_pct, so it's skipped
    rather than crashing on the division. This is safe specifically because
    no legitimate synthetic cohort can ever have starting_mrr == 0 --
    crew_size floors at 1 and PRICE_PER_TECHNICIAN is fixed and positive,
    so a real cohort going missing this way is not a risk; this only ever
    catches genuine legacy noise. Caller is responsible for counting and
    logging the skip, same as it does for deal-level skips."""
    starting_mrr = sum(
        (d["mrr_amount"] for d in deals_by_elapsed_month.get(0, []) if d["deal_type"] == "New Business"),
        start=Decimal("0"),
    )
    if starting_mrr == 0:
        return None

    cumulative = {
        "expansion_mrr": Decimal("0"),
        "reactivation_mrr": Decimal("0"),
        "contraction_mrr": Decimal("0"),
        "churn_mrr": Decimal("0"),
    }

    rows = []
    consecutive_zero_months = 0
    max_elapsed_month = max(deals_by_elapsed_month.keys(), default=0)

    elapsed_month = 0
    while True:
        calendar_month = add_months(cohort_month, elapsed_month)
        if calendar_month > TODAY.replace(day=1):
            break

        period = {
            "expansion_mrr": Decimal("0"),
            "reactivation_mrr": Decimal("0"),
            "contraction_mrr": Decimal("0"),
            "churn_mrr": Decimal("0"),
        }
        for deal in deals_by_elapsed_month.get(elapsed_month, []):
            bucket = deal["bucket"]
            if bucket == "starting_mrr" or bucket is None:
                continue  # New Business handled above (month 0 only); Renewal has no bucket
            period[bucket] += deal["mrr_amount"]

        for key in cumulative:
            cumulative[key] += period[key]

        current_mrr = (
            starting_mrr
            + cumulative["expansion_mrr"]
            + cumulative["reactivation_mrr"]
            - cumulative["contraction_mrr"]
            - cumulative["churn_mrr"]
        )

        if elapsed_month == 0:
            nrr_pct = Decimal("100.00")
            grr_pct = Decimal("100.00")
        else:
            # starting_mrr > 0 always holds under this generator's design
            # (crew_size floors at 1, PRICE_PER_TECHNICIAN > 0) -- no
            # divide-by-zero guard needed for cohorts this script ever
            # actually sees. A cohort can never be "dormant from birth."
            nrr_pct = (current_mrr / starting_mrr * 100).quantize(Decimal("0.01"))
            grr_pct = (
                (starting_mrr - cumulative["contraction_mrr"] - cumulative["churn_mrr"])
                / starting_mrr * 100
            ).quantize(Decimal("0.01"))
            # GRR is bounded 0-100% by definition in real accounting -- a
            # negative value here isn't a legitimate edge case, it's this
            # formula going out of domain. Cause: churn_mrr always reflects
            # a company's actual MRR at the moment it churns (which can
            # already include prior expansion, per this generator's own
            # churn logic), while GRR's formula compares against
            # starting_mrr alone and never adds expansion back in. A
            # company that expanded a lot before fully churning can
            # therefore show cumulative_contraction+churn > starting_mrr.
            # Clamp the derived percentage only -- the underlying
            # cumulative_contraction_mrr/cumulative_churn_mrr dollar
            # columns stay exactly as computed, since those figures are
            # accurate and Project #4 may want them un-clamped.
            # (nrr_pct has no equivalent issue and is never clamped: it's
            # bounded below by construction, since a Churn deal's amount is
            # always <= the running MRR it's subtracted from, driving
            # current_mrr to exactly 0, never negative -- confirmed both
            # algebraically and empirically against a real generator run.)
            grr_pct = max(grr_pct, Decimal("0.00"))

        rows.append({
            "cohort_month": cohort_month,
            "elapsed_month": elapsed_month,
            "calendar_month": calendar_month,
            "starting_mrr": starting_mrr,
            "expansion_mrr": period["expansion_mrr"],
            "reactivation_mrr": period["reactivation_mrr"],
            "contraction_mrr": period["contraction_mrr"],
            "churn_mrr": period["churn_mrr"],
            "cumulative_expansion_mrr": cumulative["expansion_mrr"],
            "cumulative_reactivation_mrr": cumulative["reactivation_mrr"],
            "cumulative_contraction_mrr": cumulative["contraction_mrr"],
            "cumulative_churn_mrr": cumulative["churn_mrr"],
            "current_mrr": current_mrr,
            "nrr_pct": nrr_pct,
            "grr_pct": grr_pct,
        })

        if current_mrr == 0:
            consecutive_zero_months += 1
            if consecutive_zero_months >= DORMANCY_MONTHS:
                break
        else:
            consecutive_zero_months = 0

        elapsed_month += 1

    return rows


# --------------------------------------------------------------------------
# Write phase -- Postgres, single transaction
# --------------------------------------------------------------------------

COLUMNS = [
    "cohort_month", "elapsed_month", "calendar_month", "starting_mrr",
    "expansion_mrr", "reactivation_mrr", "contraction_mrr", "churn_mrr",
    "cumulative_expansion_mrr", "cumulative_reactivation_mrr",
    "cumulative_contraction_mrr", "cumulative_churn_mrr",
    "current_mrr", "nrr_pct", "grr_pct",
]


def write_rows(rows):
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with conn:  # commits on clean exit, rolls back on exception
            with conn.cursor() as cur:
                cur.execute("DELETE FROM cohort_nrr")
                if rows:
                    values = [tuple(row[c] for c in COLUMNS) for row in rows]
                    psycopg2.extras.execute_values(
                        cur,
                        f"INSERT INTO cohort_nrr ({', '.join(COLUMNS)}) VALUES %s",
                        values,
                    )
        print(f"\nWrote {len(rows)} rows to cohort_nrr. Transaction committed.")
    except Exception:
        print("\nWrite failed -- transaction rolled back, cohort_nrr left unchanged.")
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------

def main():
    print("Fetching companies and deals from HubSpot (archived=false explicit)...")
    companies = fetch_companies()
    raw_deals = fetch_deals()
    print(f"  {len(companies)} companies, {len(raw_deals)} deals fetched.")

    valid_deals, counters = validate_deals(raw_deals)
    print_validation_summary(counters, len(raw_deals), len(valid_deals))

    cohorts, skipped_no_cohort = group_by_cohort(companies, valid_deals)
    if skipped_no_cohort:
        print(f"  Skipped {skipped_no_cohort} deals whose company has no customer_since.")
    print(f"\n{len(cohorts)} cohorts found.")

    # Companies per cohort_month, needed only to report how many companies
    # a skipped ($0 starting_mrr) cohort represents -- not used elsewhere
    # in the walk itself.
    companies_per_cohort_month = Counter()
    for customer_since in companies.values():
        if customer_since is not None:
            companies_per_cohort_month[customer_since.replace(day=1)] += 1

    all_rows = []
    skipped_zero_starting_mrr_cohorts = []
    for cohort_month in sorted(cohorts.keys()):
        rows = compute_cohort_rows(cohort_month, cohorts[cohort_month])
        if rows is None:
            skipped_zero_starting_mrr_cohorts.append(
                (cohort_month, companies_per_cohort_month.get(cohort_month, 0))
            )
            continue
        all_rows.extend(rows)

    print(f"Computed {len(all_rows)} total (cohort_month, elapsed_month) rows.")

    print(
        f"\nSkipped {len(skipped_zero_starting_mrr_cohorts)} cohort(s) with "
        f"$0 starting_mrr (no New Business deal at elapsed_month 0 -- "
        f"genuine legacy/other-project noise, never a real synthetic cohort):"
    )
    for cohort_month, company_count in skipped_zero_starting_mrr_cohorts:
        print(f"    {cohort_month}: {company_count} company(ies)")

    write_rows(all_rows)
    print("Done.")


if __name__ == "__main__":
    main()

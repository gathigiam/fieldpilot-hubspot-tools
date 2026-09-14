"""
FieldPilot Synthetic Data Generator (Project #3).

Populates the FieldPilot123 HubSpot test portal with multi-month cohort
history so the cohort_nrr Postgres table has real data to compute against.
See data-generator-spec.md for the full design rationale.

Throwaway/seed-script territory per repo convention -- run once (or re-run
against a wiped portal), not meant to be imported elsewhere.

Runs via the "FieldPilot Scripts" Service Key (HUBSPOT_TOKEN in .env) --
NOT the read-only Claude/HubSpot MCP connector.

All fixed mechanics live here. Every value the spec marks "adjust freely"
lives in synthetic_data_config.py instead -- see that file to change
simulation shape without touching logic.
"""

import argparse
import calendar
import json
import os
import random
import string
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

import synthetic_data_config as cfg

load_dotenv()
TOKEN = os.environ.get("HUBSPOT_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}
BASE_URL = "https://api.hubapi.com"
BATCH_SIZE = 100  # HubSpot's documented cap for /batch/* endpoints

TODAY = date.today()

# Stamped on every Company/Deal this run creates (synthetic_run_id, a new
# custom property on both objects -- see run_preflight_checks below). Lets a
# later run's cleanup_synthetic_data.py find and archive exactly this run's
# records instead of layering synthetic cohorts across multiple runs.
RUN_ID = f"run-{TODAY.isoformat()}-{''.join(random.choices(string.ascii_lowercase + string.digits, k=6))}"

# deal_type -> formula bucket, per spec. Fixed mechanics, not tunable --
# lives here, not in config.
DEAL_TYPE_BUCKET = {
    "New Business": "starting_mrr",
    "Expansion": "expansion_mrr",
    "Downgrade": "contraction_mrr",
    "Churn": "churn_mrr",
    "Reactivation": "reactivation_mrr",
    "Renewal": None,  # $0 MRR impact -- still generated, see spec
}


# --------------------------------------------------------------------------
# Pre-flight
# --------------------------------------------------------------------------

def _property_exists(object_type, property_name):
    resp = requests.get(
        f"{BASE_URL}/crm/v3/properties/{object_type}/{property_name}", headers=HEADERS
    )
    return resp.status_code == 200, resp


def run_preflight_checks():
    """Checks every HubSpot-side prerequisite this script depends on and
    reports ALL problems found in one pass, not just the first -- five
    separate setup items have accumulated across this project's sessions
    (deal_type's two new options, synthetic_run_id on two objects,
    subscription_status, churned_date), and discovering them one at a time
    across repeated broken runs wastes API calls and your time. See
    fieldpilot-schema-checklist.md for what each of these is for."""
    problems = []

    resp = requests.get(f"{BASE_URL}/crm/v3/properties/deals/deal_type", headers=HEADERS)
    if resp.status_code != 200:
        problems.append(
            f"Could not read deal_type property metadata (HTTP {resp.status_code}) "
            f"-- can't verify its options at all."
        )
    else:
        options = {o["value"] for o in resp.json().get("options", [])}
        missing = {"Reactivation", "Churn"} - options
        if missing:
            problems.append(
                f"deal_type is missing option(s): {sorted(missing)}. Add them in the "
                "HubSpot UI -- see data-generator-spec.md's BLOCKING ACTION section."
            )

    for object_type in ("companies", "deals"):
        ok, resp = _property_exists(object_type, "synthetic_run_id")
        if not ok:
            problems.append(
                f"synthetic_run_id property missing on {object_type} (HTTP "
                f"{resp.status_code}). Add a custom String property named "
                f"synthetic_run_id to {object_type} in the HubSpot UI."
            )

    for property_name in ("subscription_status", "churned_date"):
        ok, resp = _property_exists("companies", property_name)
        if not ok:
            problems.append(
                f"{property_name} property missing on companies (HTTP "
                f"{resp.status_code}). Add it in the HubSpot UI -- see "
                "fieldpilot-schema-checklist.md."
            )

    if problems:
        message = "Pre-flight found {} problem(s) -- fix all of these before re-running:\n".format(
            len(problems)
        )
        message += "\n".join(f"  {i}. {p}" for i, p in enumerate(problems, start=1))
        raise SystemExit(message)

    print("Pre-flight OK: all 5 required properties/options are in place.")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def add_months(d, months):
    """Calendar-correct month add, clamping day-of-month to the target
    month's length (e.g. Jan 31 + 1 month -> Feb 28/29)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def random_date_between(start, end):
    if end <= start:
        return start
    delta_days = (end - start).days
    return start + timedelta(days=random.randint(0, delta_days))


def tier_for_crew_size(crew_size):
    """Which tier's range crew_size currently falls in. Used to detect
    migration after an Expansion/Downgrade. Falls back to the nearest tier
    if crew_size sits above every declared range (Enterprise's upper bound
    is a soft ceiling, not a hard cap)."""
    for tier, (lo, hi) in cfg.TIER_CREW_SIZE_RANGES.items():
        if lo <= crew_size <= hi:
            return tier
    # Above every range (soft ceiling exceeded) -> stays Enterprise.
    return "Enterprise"


def draw_weighted_tier():
    tiers, weights = zip(*cfg.TIER_WEIGHTS.items())
    return random.choices(tiers, weights=weights, k=1)[0]


def renewal_probabilities_for(tier):
    return (
        cfg.ANNUAL_RENEWAL_PROBABILITIES
        if cfg.TIER_CADENCE_MONTHS[tier] == 12
        else cfg.MONTHLY_RENEWAL_PROBABILITIES
    )


def roll_renewal_outcome(tier):
    probs = renewal_probabilities_for(tier)
    outcomes, weights = zip(*probs.items())
    return random.choices(outcomes, weights=weights, k=1)[0]


def roll_reactivation_outcome():
    outcomes, weights = zip(*cfg.REACTIVATION_SPLIT.items())
    return random.choices(outcomes, weights=weights, k=1)[0]


# --------------------------------------------------------------------------
# In-memory dataset being built. Populated entirely before any HTTP call.
# --------------------------------------------------------------------------

companies = []          # list of company dicts (temp_id, properties, ...)
deals = []               # list of deal dicts (temp_id, properties, company_temp_id)
winback_links = []       # list of (old_company_temp_id, new_company_temp_id)

_next_temp_id = [0]


def _new_temp_id():
    _next_temp_id[0] += 1
    return _next_temp_id[0]


def _stamped(properties):
    """Every created record's properties dict passes through here so
    synthetic_run_id is never missed at a creation site."""
    return {**properties, "synthetic_run_id": RUN_ID}


def apply_churn_state(company, churned_date):
    """The one function that touches churn-related fields, so
    subscription_status / churned_date / monthly_recurring_revenue can't
    drift apart. Company keeps its crew_size as history, but MRR is zeroed."""
    company["properties"]["subscription_status"] = "Churned"
    company["properties"]["churned_date"] = churned_date.isoformat()
    company["properties"]["monthly_recurring_revenue"] = 0


def reactivate_in_place(company, reactivation_date):
    """In-grace-window path: same company record, same cohort. Restores the
    MRR/status the account had right before it churned and creates a
    Reactivation deal. Distinct code path from spawning a new cohort below --
    not the same function applied with different flags."""
    restored_mrr = company["properties"]["crew_size"] * cfg.PRICE_PER_TECHNICIAN
    company["properties"]["subscription_status"] = "Active"
    company["properties"]["churned_date"] = ""
    company["properties"]["monthly_recurring_revenue"] = restored_mrr

    deal_temp_id = _new_temp_id()
    deals.append({
        "temp_id": deal_temp_id,
        "company_temp_id": company["temp_id"],
        "properties": _stamped({
            "deal_type": "Reactivation",
            "mrr_amount": restored_mrr,
            # temp_id suffix makes dealname genuinely unique per deal, not
            # just per company+type -- needed so batch_create_deals() can
            # match each create response back to its request by dealname
            # rather than by (unreliable, see that function's docstring)
            # response list position. A company can have many Reactivation
            # deals' siblings (Renewal, Expansion, Downgrade fire
            # repeatedly over a company's life) sharing the same
            # company+type, so temp_id is what actually disambiguates.
            "dealname": f"{company['properties']['name']} - Reactivation #{deal_temp_id}",
            "closedate": reactivation_date.isoformat(),
        }),
    })
    return reactivation_date


def create_new_company(signup_month_date, winback_source_temp_id=None):
    """The one and only company-creation path -- used for regular monthly
    cohort spawning AND for after-grace-window reactivation (a fresh signup
    in every respect: new customer_since, new cohort, re-rolled tier/crew_size,
    New Business deal type). winback_source_temp_id, if given, is recorded
    for the human-metadata company-to-company association only -- see
    winback_links below; the cohort/NRR simulation never reads it back."""
    tier = draw_weighted_tier()
    lo, hi = cfg.TIER_CREW_SIZE_RANGES[tier]
    crew_size = random.randint(lo, hi)
    starting_mrr = crew_size * cfg.PRICE_PER_TECHNICIAN

    temp_id = _new_temp_id()
    company = {
        "temp_id": temp_id,
        "properties": _stamped({
            "name": f"Synthetic Co {temp_id:04d}",
            "customer_since": signup_month_date.isoformat(),
            "plan_tier": tier,
            "crew_size": crew_size,
            "monthly_recurring_revenue": starting_mrr,
            "subscription_status": "Active",
            "churned_date": "",
        }),
    }
    companies.append(company)

    deal_temp_id = _new_temp_id()
    deals.append({
        "temp_id": deal_temp_id,
        "company_temp_id": company["temp_id"],
        "properties": _stamped({
            "deal_type": "New Business",
            "mrr_amount": starting_mrr,
            "dealname": f"{company['properties']['name']} - New Business #{deal_temp_id}",
            "closedate": signup_month_date.isoformat(),
        }),
    })

    if winback_source_temp_id is not None:
        winback_links.append((winback_source_temp_id, company["temp_id"]))

    simulate_company_forward(company, next_renewal_date=add_months(
        signup_month_date, cfg.TIER_CADENCE_MONTHS[tier]
    ))
    return company


def simulate_company_forward(company, next_renewal_date):
    """Walks renewal decision points forward from next_renewal_date to the
    present-day cutoff, mutating `company` in place and appending to the
    module-level `deals` list. Stops early if the company churns and never
    reactivates within this call (churn's own reactivation branches make
    their own recursive/forward calls where applicable)."""
    while next_renewal_date <= TODAY:
        tier = company["properties"]["plan_tier"]
        crew_size = company["properties"]["crew_size"]

        # Renewal is the decision point itself -- $0 impact, always created.
        renewal_temp_id = _new_temp_id()
        deals.append({
            "temp_id": renewal_temp_id,
            "company_temp_id": company["temp_id"],
            "properties": _stamped({
                "deal_type": "Renewal",
                "mrr_amount": 0,
                "dealname": f"{company['properties']['name']} - Renewal #{renewal_temp_id}",
                "closedate": next_renewal_date.isoformat(),
            }),
        })

        outcome = roll_renewal_outcome(tier)

        if outcome == "flat":
            next_renewal_date = add_months(next_renewal_date, cfg.TIER_CADENCE_MONTHS[tier])
            continue

        if outcome == "expansion":
            seats = random.randint(*cfg.EXPANSION_SEATS_RANGE)
            new_crew_size = crew_size + seats
            mrr_delta = seats * cfg.PRICE_PER_TECHNICIAN

            company["properties"]["crew_size"] = new_crew_size
            company["properties"]["monthly_recurring_revenue"] += mrr_delta

            expansion_temp_id = _new_temp_id()
            deals.append({
                "temp_id": expansion_temp_id,
                "company_temp_id": company["temp_id"],
                "properties": _stamped({
                    "deal_type": "Expansion",
                    "mrr_amount": mrr_delta,
                    "crew_size_requested": seats,
                    "dealname": f"{company['properties']['name']} - Expansion #{expansion_temp_id}",
                    "closedate": next_renewal_date.isoformat(),
                }),
            })

            new_tier = tier_for_crew_size(new_crew_size)
            if new_tier != tier:
                company["properties"]["plan_tier"] = new_tier
                # Unconditional clock reset on any tier change, even between
                # two tiers sharing a cadence -- simpler than conditionally
                # checking whether the cadence itself changed.
                next_renewal_date = add_months(next_renewal_date, cfg.TIER_CADENCE_MONTHS[new_tier])
            else:
                next_renewal_date = add_months(next_renewal_date, cfg.TIER_CADENCE_MONTHS[tier])
            continue

        if outcome == "downgrade":
            seats = random.randint(*cfg.DOWNGRADE_SEATS_RANGE)
            new_crew_size = crew_size - seats

            if new_crew_size <= 0:
                # Floor is 1, never 0 -- a downgrade that would zero or
                # go negative resolves to Churn for the full remaining
                # crew_size/MRR instead of a capped Downgrade. Solo-tier
                # companies (crew_size already 1) always land here, with
                # no special case needed.
                _handle_churn(company, next_renewal_date)
                return
            else:
                mrr_delta = seats * cfg.PRICE_PER_TECHNICIAN
                company["properties"]["crew_size"] = new_crew_size
                company["properties"]["monthly_recurring_revenue"] -= mrr_delta

                downgrade_temp_id = _new_temp_id()
                deals.append({
                    "temp_id": downgrade_temp_id,
                    "company_temp_id": company["temp_id"],
                    "properties": _stamped({
                        "deal_type": "Downgrade",
                        "mrr_amount": mrr_delta,
                        "crew_size_requested": seats,
                        "dealname": f"{company['properties']['name']} - Downgrade #{downgrade_temp_id}",
                        "closedate": next_renewal_date.isoformat(),
                    }),
                })

                new_tier = tier_for_crew_size(new_crew_size)
                if new_tier != tier:
                    company["properties"]["plan_tier"] = new_tier
                    next_renewal_date = add_months(next_renewal_date, cfg.TIER_CADENCE_MONTHS[new_tier])
                else:
                    next_renewal_date = add_months(next_renewal_date, cfg.TIER_CADENCE_MONTHS[tier])
            continue

        if outcome == "churn":
            _handle_churn(company, next_renewal_date)
            return


def _handle_churn(company, churn_date):
    """Shared churn handling for both a rolled Churn outcome and a
    downgrade-below-floor resolving to Churn. Creates the Churn deal for the
    full remaining crew_size/MRR, applies churn state, then rolls what
    happens next per REACTIVATION_SPLIT."""
    remaining_mrr = company["properties"]["monthly_recurring_revenue"]

    churn_temp_id = _new_temp_id()
    deals.append({
        "temp_id": churn_temp_id,
        "company_temp_id": company["temp_id"],
        "properties": _stamped({
            "deal_type": "Churn",
            "mrr_amount": remaining_mrr,
            "dealname": f"{company['properties']['name']} - Churn #{churn_temp_id}",
            "closedate": churn_date.isoformat(),
        }),
    })

    apply_churn_state(company, churn_date)

    reactivation_outcome = roll_reactivation_outcome()

    if reactivation_outcome == "in_grace_window":
        window_end = min(
            add_months(churn_date, cfg.REACTIVATION_GRACE_WINDOW_MONTHS), TODAY
        )
        if window_end <= churn_date:
            return  # window has already elapsed as of TODAY -- no time left to reactivate
        reactivation_date = random_date_between(churn_date, window_end)
        next_renewal_date = reactivate_in_place(company, reactivation_date)
        simulate_company_forward(
            company,
            next_renewal_date=add_months(
                reactivation_date, cfg.TIER_CADENCE_MONTHS[company["properties"]["plan_tier"]]
            ),
        )

    elif reactivation_outcome == "after_grace_window":
        window_end = add_months(churn_date, cfg.REACTIVATION_GRACE_WINDOW_MONTHS)
        if window_end >= TODAY:
            return  # no time left after the window before the cutoff
        reactivation_date = random_date_between(window_end, TODAY)
        create_new_company(reactivation_date, winback_source_temp_id=company["temp_id"])

    # "never" -- old record stays Churned permanently, nothing further.


# --------------------------------------------------------------------------
# Simulation driver
# --------------------------------------------------------------------------

def run_simulation():
    cohort_start = add_months(TODAY, -cfg.COHORT_MONTHS)
    for month_offset in range(cfg.COHORT_MONTHS):
        month_date = add_months(cohort_start, month_offset)
        for _ in range(cfg.COMPANIES_PER_MONTH):
            create_new_company(month_date)

    churn_count = sum(1 for d in deals if d["properties"]["deal_type"] == "Churn")
    reactivation_count = sum(1 for d in deals if d["properties"]["deal_type"] == "Reactivation")
    winback_count = len(winback_links)
    print(
        f"Simulation built {len(companies)} companies and {len(deals)} deals in memory."
    )
    print(
        f"  Churns: {churn_count}  |  In-window reactivations: {reactivation_count} "
        f"({reactivation_count / churn_count:.1%} of churns, target "
        f"{cfg.REACTIVATION_SPLIT['in_grace_window']:.0%})  |  "
        f"After-window win-backs: {winback_count} "
        f"({winback_count / churn_count:.1%} of churns, target "
        f"{cfg.REACTIVATION_SPLIT['after_grace_window']:.0%})"
        if churn_count else "  No churns this run."
    )
    if churn_count and winback_count / churn_count < cfg.REACTIVATION_SPLIT["after_grace_window"]:
        print(
            "  Note: after-window win-backs read below target by design -- churns near "
            f"the end of the {cfg.COHORT_MONTHS}-month window don't have "
            f"{cfg.REACTIVATION_GRACE_WINDOW_MONTHS}+ months of runway left before today "
            "for a win-back to land on time, so some of that 10% roll is absorbed into "
            "\"no time left\" rather than fabricating a future-dated signup. See "
            "REACTIVATION_SPLIT in synthetic_data_config.py."
        )


# --------------------------------------------------------------------------
# Batch write phase
# --------------------------------------------------------------------------

def chunked(items, size=BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def company_create_payloads():
    """The exact batch/create request bodies for companies, chunked. Shared
    by the real write path and --dry-run so the dry-run output is literally
    what would be sent, not a reconstruction of it."""
    return [
        {"inputs": [{"properties": c["properties"]} for c in batch]}
        for batch in chunked(companies)
    ]


def deal_create_payloads():
    return [
        {"inputs": [{"properties": d["properties"]} for d in batch]}
        for batch in chunked(deals)
    ]


def association_payloads(pairs):
    """pairs: list of (from_hubspot_id, to_hubspot_id) -- or, pre-write, of
    (from_temp_id, to_temp_id) for dry-run preview purposes."""
    return [
        {"inputs": [{"from": {"id": f}, "to": {"id": t}} for f, t in batch]}
        for batch in chunked(pairs)
    ]


def batch_create_companies():
    """Matches each batch/create response entry back to its request by the
    company's `name` property, NOT by response list position.

    HubSpot's v3 batch/create endpoints do not guarantee the results array
    preserves input order (confirmed via HubSpot community reports of
    results being returned out of order/shuffled) -- zip(batch,
    resp.json()["results"]) silently paired inputs with whatever response
    happened to land at the same index, which produced real
    deal-to-wrong-company corruption in this portal on 2026-09-14 (run
    run-2026-09-14-vfch7d) before this was caught and fixed. `name` is
    unique per company by construction (one row per company, no
    duplicates), so it's a safe correlation key -- confirmed live that
    batch/create echoes back the full `properties` sent, `name` included."""
    temp_id_to_hubspot_id = {}
    for batch, payload in zip(chunked(companies), company_create_payloads()):
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/create",
            headers=HEADERS, json=payload,
        )
        print(f"Created company batch of {len(batch)}: {resp.status_code}")
        if resp.status_code >= 300:
            raise SystemExit(f"Company batch create failed: {resp.text}")

        temp_company_by_name = {c["properties"]["name"]: c for c in batch}
        results = resp.json()["results"]
        if len(results) != len(batch):
            raise SystemExit(
                f"Company batch create returned {len(results)} results for "
                f"{len(batch)} inputs -- can't safely match. Aborting."
            )
        for result in results:
            result_name = result["properties"]["name"]
            temp_company = temp_company_by_name.pop(result_name, None)
            if temp_company is None:
                raise SystemExit(
                    f"Company batch create returned unrecognized name "
                    f"{result_name!r} -- can't match to a request. Aborting."
                )
            temp_id_to_hubspot_id[temp_company["temp_id"]] = result["id"]
    return temp_id_to_hubspot_id


def batch_create_deals():
    """Matches each batch/create response entry back to its request by the
    deal's `dealname` property, NOT by response list position -- same
    unreliable-ordering issue and fix as batch_create_companies() above.
    `dealname` is unique per deal because every dealname site in this file
    includes that deal's own temp_id as a suffix (deal_type alone repeats
    many times per company -- Renewal fires every cadence period, e.g.)."""
    temp_id_to_hubspot_id = {}
    for batch, payload in zip(chunked(deals), deal_create_payloads()):
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/deals/batch/create",
            headers=HEADERS, json=payload,
        )
        print(f"Created deal batch of {len(batch)}: {resp.status_code}")
        if resp.status_code >= 300:
            raise SystemExit(f"Deal batch create failed: {resp.text}")

        temp_deal_by_name = {d["properties"]["dealname"]: d for d in batch}
        results = resp.json()["results"]
        if len(results) != len(batch):
            raise SystemExit(
                f"Deal batch create returned {len(results)} results for "
                f"{len(batch)} inputs -- can't safely match. Aborting."
            )
        for result in results:
            result_name = result["properties"]["dealname"]
            temp_deal = temp_deal_by_name.pop(result_name, None)
            if temp_deal is None:
                raise SystemExit(
                    f"Deal batch create returned unrecognized dealname "
                    f"{result_name!r} -- can't match to a request. Aborting."
                )
            temp_id_to_hubspot_id[temp_deal["temp_id"]] = result["id"]
    return temp_id_to_hubspot_id


def batch_associate_default(from_object_type, to_object_type, pairs):
    """pairs: list of (from_hubspot_id, to_hubspot_id)."""
    for batch, payload in zip(chunked(pairs), association_payloads(pairs)):
        resp = requests.post(
            f"{BASE_URL}/crm/v4/associations/{from_object_type}/{to_object_type}/batch/associate/default",
            headers=HEADERS, json=payload,
        )
        print(
            f"Associated {from_object_type}->{to_object_type} batch of "
            f"{len(batch)}: {resp.status_code}"
        )
        if resp.status_code >= 300:
            raise SystemExit(f"Association batch failed: {resp.text}")


def write_to_hubspot():
    company_ids = batch_create_companies()
    deal_ids = batch_create_deals()

    deal_company_pairs = [
        (deal_ids[d["temp_id"]], company_ids[d["company_temp_id"]]) for d in deals
    ]
    batch_associate_default("deals", "companies", deal_company_pairs)

    if winback_links:
        # Human/analysis metadata only (future "true win-back rate"
        # question) -- same rationale as keeping $0-impact Renewal deals.
        # The NRR/cohort calculation must key strictly off each company's
        # own customer_since and must NEVER traverse this association.
        winback_pairs = [
            (company_ids[old_id], company_ids[new_id])
            for old_id, new_id in winback_links
        ]
        batch_associate_default("companies", "companies", winback_pairs)


def write_dry_run_output(path):
    """Writes the exact batch payloads that a real run would send to
    HubSpot, without calling the network. Association payloads use this
    run's temp_ids in place of real HubSpot record IDs (which don't exist
    yet in a dry run) -- labeled as such so they're never mistaken for
    real IDs when eyeballing the file."""
    deal_company_temp_pairs = [
        (d["temp_id"], d["company_temp_id"]) for d in deals
    ]
    winback_temp_pairs = list(winback_links)

    output = {
        "run_id": RUN_ID,
        "counts": {
            "companies": len(companies),
            "deals": len(deals),
            "winback_links": len(winback_links),
        },
        "company_batches": company_create_payloads(),
        "deal_batches": deal_create_payloads(),
        "deal_to_company_association_batches (temp_ids, not real HubSpot IDs)":
            association_payloads(deal_company_temp_pairs),
        "winback_company_to_company_association_batches (temp_ids, not real HubSpot IDs)":
            association_payloads(winback_temp_pairs),
    }
    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n--dry-run: wrote full payloads to {path}")
    print(f"Sample company payload:\n{json.dumps(companies[0]['properties'], indent=2)}")
    sample_deal = next(d for d in deals if d["properties"]["deal_type"] == "New Business")
    print(f"\nSample deal payload:\n{json.dumps(sample_deal['properties'], indent=2)}")
    print("\nNo network calls made. Nothing was created in HubSpot.")


# --------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build the dataset and write the exact request payloads to a "
             "local JSON file instead of calling the HubSpot API.",
    )
    parser.add_argument(
        "--dry-run-output", default="dry_run_output.json",
        help="Path to write dry-run payloads to (default: dry_run_output.json).",
    )
    args = parser.parse_args()

    if not args.dry_run:
        run_preflight_checks()

    run_simulation()
    print(f"Run ID: {RUN_ID}")

    if args.dry_run:
        write_dry_run_output(args.dry_run_output)
    else:
        write_to_hubspot()
        print("Done.")

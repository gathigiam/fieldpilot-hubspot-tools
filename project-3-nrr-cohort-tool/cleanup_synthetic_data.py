"""
Cleanup for a single run of generate_synthetic_data.py.

Deliberately a separate script from the generator, not a --cleanup flag on
it -- keeps the destructive path from being reachable by a flag mistake.

Finds every Company and Deal stamped with a given synthetic_run_id (see
generate_synthetic_data.py's RUN_ID) via the Search API and archives them
(HubSpot's batch/archive -- a soft delete: records go to the recycling bin,
restorable for 90 days, then auto-purged; excluded from default list/search
results immediately, which is what matters for not layering synthetic
cohorts across runs). Deals are archived before companies so a failure
partway through never leaves deals pointing at an already-archived company.

Usage:
    python cleanup_synthetic_data.py --run-id run-2026-09-14-abc123 --dry-run
    python cleanup_synthetic_data.py --run-id run-2026-09-14-abc123
"""

import argparse
import os

import requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ.get("HUBSPOT_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}
BASE_URL = "https://api.hubapi.com"
BATCH_SIZE = 100  # HubSpot's documented cap for /batch/* and /search results per page


def chunked(items, size=BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def find_ids_by_run_id(object_type, run_id):
    """Pages through the Search API for every record with this
    synthetic_run_id. Search results default to archived=false / active
    records only, which is exactly what we want here -- a run's records
    haven't been archived yet when this is called."""
    ids = []
    after = None
    while True:
        payload = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "synthetic_run_id",
                    "operator": "EQ",
                    "value": run_id,
                }]
            }],
            "properties": ["synthetic_run_id"],
            "limit": BATCH_SIZE,
        }
        if after:
            payload["after"] = after

        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/{object_type}/search",
            headers=HEADERS, json=payload,
        )
        if resp.status_code != 200:
            raise SystemExit(
                f"Search failed for {object_type} (HTTP {resp.status_code}): {resp.text}"
            )
        body = resp.json()
        ids.extend(r["id"] for r in body.get("results", []))

        paging = body.get("paging", {}).get("next")
        if not paging:
            break
        after = paging["after"]

    return ids


def batch_archive(object_type, ids):
    for batch in chunked(ids):
        payload = {"inputs": [{"id": i} for i in batch]}
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/{object_type}/batch/archive",
            headers=HEADERS, json=payload,
        )
        print(f"Archived {object_type} batch of {len(batch)}: {resp.status_code}")
        if resp.status_code >= 300:
            raise SystemExit(f"{object_type} batch archive failed: {resp.text}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="The synthetic_run_id to clean up.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be archived (counts and sample IDs) without archiving anything.",
    )
    args = parser.parse_args()

    print(f"Searching for records with synthetic_run_id = {args.run_id} ...")
    deal_ids = find_ids_by_run_id("deals", args.run_id)
    company_ids = find_ids_by_run_id("companies", args.run_id)

    print(f"Found {len(deal_ids)} deals, {len(company_ids)} companies.")

    if not deal_ids and not company_ids:
        print("Nothing found for this run-id. Nothing to do.")
        return

    if args.dry_run:
        print("\n--dry-run: nothing archived.")
        print(f"Sample deal IDs: {deal_ids[:10]}")
        print(f"Sample company IDs: {company_ids[:10]}")
        return

    # Deals first -- a failure partway through never leaves deals pointing
    # at an already-archived company.
    batch_archive("deals", deal_ids)
    batch_archive("companies", company_ids)
    print("Done.")


if __name__ == "__main__":
    main()

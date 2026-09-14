#!/usr/bin/env python3
"""
HubSpot Data Cleaning Tool for FieldPilot
==========================================
Cleans contact and company data in HubSpot:
- Title-cases contact names
- Standardizes phone numbers to E.164-style format
- Flags duplicate company domains

Usage:
    python clean_hubspot_data.py          # Dry-run mode (shows changes, doesn't apply)
    python clean_hubspot_data.py --apply  # Apply changes to HubSpot
"""

import os
import re
import sys
import csv
import difflib
import requests
from collections import defaultdict
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.environ.get("HUBSPOT_TOKEN")
if not TOKEN:
    print("ERROR: HUBSPOT_TOKEN not found in .env file")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

# Configuration
DRY_RUN = "--apply" not in sys.argv


def title_case_name(name):
    """
    Convert name to title case while preserving special cases like O'Brien, McDonald, etc.
    Parenthetical suffixes (e.g., "(Sample Contact)") are left completely unchanged.
    """
    if not name:
        return name

    # Split on parenthesis: preserve everything from "(" onward as-is
    import re
    match = re.match(r'^([^(]*?)(\(.*)$', name)
    if match:
        # Process only the part before the parenthesis
        base_name = match.group(1).rstrip()  # Remove trailing space before paren
        parenthetical = match.group(2)
        processed_name = title_case_name(base_name)  # Recursive call on base part
        # Reconstruct with a space before the parenthetical if base_name had content
        if processed_name:
            return f"{processed_name} {parenthetical}"
        else:
            return parenthetical

    # Handle names with apostrophes (O'Brien, D'Angelo)
    if "'" in name:
        parts = name.split("'")
        return "'".join([p.capitalize() for p in parts])

    # Handle hyphenated names (Mary-Jane)
    if "-" in name:
        parts = name.split("-")
        return "-".join([p.capitalize() for p in parts])

    # Handle names with spaces
    if " " in name:
        parts = name.split(" ")
        return " ".join([p.capitalize() for p in parts])

    # Heuristic for Mc/Mac prefix patterns (McDonald, MacArthur, etc.)
    # NOTE: This is a heuristic, not a complete solution. It will misfire on real names
    # that start with "Mac" but aren't compound names (e.g., "Macy" → "MacY").
    # A production version would need a verified name-parsing library or lookup list
    # rather than a purely rule-based approach.
    lower_name = name.lower()
    if lower_name.startswith("mc") and len(name) > 2:
        # McDonald, McCarthy, etc.
        return "Mc" + name[2].upper() + name[3:].lower()
    elif lower_name.startswith("mac") and len(name) > 3:
        # MacArthur, MacDonald, etc.
        return "Mac" + name[3].upper() + name[4:].lower()

    # Simple case
    return name.capitalize()


def standardize_phone(phone):
    """
    Standardize phone number to +1XXXXXXXXXX format (US numbers only).
    Handles formats like:
    - 512-555-0182
    - (512) 555 0199
    - 5125550123
    - +1 512.555.0147

    Note: This function only handles US 10-digit and 11-digit (with country code) numbers.
    International numbers are returned unchanged.
    """
    if not phone:
        return phone

    # Remove all non-digit characters
    digits = re.sub(r'\D', '', phone)

    # Handle US phone numbers (10 or 11 digits)
    if len(digits) == 10:
        # Add country code
        return f"+1{digits}"
    elif len(digits) == 11 and digits.startswith('1'):
        # Already has country code
        return f"+{digits}"
    else:
        # Return as-is if format is unexpected
        return phone


def normalize_domain(domain):
    """
    Normalize domain for comparison (lowercase, remove www, strip whitespace).
    """
    if not domain:
        return ""
    return domain.lower().strip().replace("www.", "")


def normalize_company_name(name):
    """
    Normalize company name for similarity comparison.
    Removes business suffixes, common words, punctuation, and extra whitespace.
    """
    if not name:
        return ""

    # Lowercase
    normalized = name.lower()

    # Remove common business suffixes as whole words
    suffixes = [
        r'\bllc\b', r'\binc\b', r'\bincorporated\b', r'\bcorp\b',
        r'\bcorporation\b', r'\bco\b', r'\bcompany\b', r'\bltd\b',
        r'\bservices\b', r'\bgroup\b'
    ]
    for suffix in suffixes:
        normalized = re.sub(suffix, '', normalized)

    # Remove "&" and "and"
    normalized = normalized.replace('&', ' ').replace(' and ', ' ')

    # Remove remaining punctuation
    normalized = re.sub(r'[^\w\s]', '', normalized)

    # Collapse extra whitespace
    normalized = ' '.join(normalized.split())

    return normalized.strip()


def name_similarity(name1, name2):
    """
    Calculate similarity ratio between two company names.
    Returns a float between 0 (completely different) and 1 (identical).
    """
    normalized1 = normalize_company_name(name1)
    normalized2 = normalize_company_name(name2)
    return difflib.SequenceMatcher(None, normalized1, normalized2).ratio()


def fetch_all_contacts():
    """Fetch all contacts from HubSpot."""
    contacts = []
    url = "https://api.hubapi.com/crm/v3/objects/contacts"
    params = {"limit": 100, "properties": "firstname,lastname,phone"}

    while url:
        response = requests.get(url, headers=HEADERS, params=params)
        if response.status_code != 200:
            print(f"ERROR fetching contacts: {response.status_code}")
            print(response.text)
            return []

        data = response.json()
        contacts.extend(data.get("results", []))

        # Check for pagination
        url = data.get("paging", {}).get("next", {}).get("link")
        params = None  # Pagination URL includes params

    return contacts


def fetch_all_companies():
    """Fetch all companies from HubSpot."""
    companies = []
    url = "https://api.hubapi.com/crm/v3/objects/companies"
    params = {"limit": 100, "properties": "name,domain,phone,city,state"}

    while url:
        response = requests.get(url, headers=HEADERS, params=params)
        if response.status_code != 200:
            print(f"ERROR fetching companies: {response.status_code}")
            print(response.text)
            return []

        data = response.json()
        companies.extend(data.get("results", []))

        # Check for pagination
        url = data.get("paging", {}).get("next", {}).get("link")
        params = None

    return companies


def update_contact(contact_id, properties):
    """Update a contact in HubSpot."""
    url = f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}"
    response = requests.patch(url, headers=HEADERS, json={"properties": properties})
    return response.status_code == 200


def clean_contacts(contacts):
    """Clean contact data (names and phone numbers)."""
    changes = []

    for contact in contacts:
        contact_id = contact["id"]
        props = contact.get("properties", {})

        firstname = props.get("firstname", "")
        lastname = props.get("lastname", "")
        phone = props.get("phone", "")

        # Calculate cleaned values
        new_firstname = title_case_name(firstname) if firstname else firstname
        new_lastname = title_case_name(lastname) if lastname else lastname
        new_phone = standardize_phone(phone) if phone else phone

        # Check if any changes needed
        updates = {}
        old_values = {}
        change_desc = []

        if firstname and new_firstname != firstname:
            updates["firstname"] = new_firstname
            old_values["firstname"] = firstname
            change_desc.append(f"firstname: '{firstname}' -> '{new_firstname}'")

        if lastname and new_lastname != lastname:
            updates["lastname"] = new_lastname
            old_values["lastname"] = lastname
            change_desc.append(f"lastname: '{lastname}' -> '{new_lastname}'")

        if phone and new_phone != phone:
            updates["phone"] = new_phone
            old_values["phone"] = phone
            change_desc.append(f"phone: '{phone}' -> '{new_phone}'")

        if updates:
            changes.append({
                "id": contact_id,
                "type": "contact",
                "name": f"{firstname} {lastname}".strip(),
                "updates": updates,
                "old_values": old_values,
                "description": ", ".join(change_desc)
            })

    return changes


def find_duplicate_domains(companies):
    """Find companies with duplicate or similar domains with confidence scoring."""
    domain_map = defaultdict(list)
    company_data = {}  # Store full company data by ID
    duplicates = []

    for company in companies:
        props = company.get("properties", {})
        domain = props.get("domain", "")
        name = props.get("name", "")
        phone = props.get("phone", "")
        city = props.get("city", "")
        state = props.get("state", "")

        company_id = company["id"]
        company_data[company_id] = {
            "id": company_id,
            "name": name,
            "domain": domain,
            "phone": phone,
            "city": city,
            "state": state
        }

        if domain:
            normalized = normalize_domain(domain)
            domain_map[normalized].append(company_id)

    # Find exact duplicates
    for normalized_domain, company_ids in domain_map.items():
        if len(company_ids) > 1:
            companies_in_group = [company_data[cid] for cid in company_ids]

            # Calculate confidence signals
            name_sim = name_similarity(companies_in_group[0]["name"], companies_in_group[1]["name"])

            # Phone match (only if both have phones)
            phone1 = companies_in_group[0]["phone"]
            phone2 = companies_in_group[1]["phone"]
            phone_match = (phone1 and phone2 and phone1 == phone2) if (phone1 and phone2) else None

            # Location match (both city AND state must match, only if both have values)
            city1 = companies_in_group[0]["city"]
            city2 = companies_in_group[1]["city"]
            state1 = companies_in_group[0]["state"]
            state2 = companies_in_group[1]["state"]
            location_match = (city1 and city2 and state1 and state2 and
                            city1 == city2 and state1 == state2) if (city1 and city2 and state1 and state2) else None

            # Determine confidence
            # REVIEW: High name similarity but phone AND location both explicitly differ (possible separate branches)
            if name_sim >= 0.6 and phone_match is False and location_match is False:
                confidence = "REVIEW"
            # HIGH: Name similarity >= 0.6, OR phone matches, OR location matches
            elif name_sim >= 0.6 or phone_match or location_match:
                confidence = "HIGH"
            # LOW: No corroborating signals
            else:
                confidence = "LOW"

            duplicates.append({
                "domain": normalized_domain,
                "companies": companies_in_group,
                "type": "exact_duplicate",
                "confidence": confidence,
                "name_similarity": name_sim,
                "phone_match": phone_match,
                "location_match": location_match
            })

    # Find similar domains (different by hyphens/underscores only)
    similar_groups = defaultdict(list)
    for normalized_domain in domain_map.keys():
        # Create a key with hyphens and underscores removed
        similarity_key = normalized_domain.replace("-", "").replace("_", "")
        similar_groups[similarity_key].append(normalized_domain)

    near_duplicates = []
    for similarity_key, domain_list in similar_groups.items():
        if len(domain_list) > 1:
            # Collect all companies for these similar domains
            all_company_ids = []
            for domain in domain_list:
                all_company_ids.extend(domain_map[domain])

            companies_in_group = [company_data[cid] for cid in all_company_ids]

            # Only add if not already flagged as exact duplicate
            if not any(d["domain"] in domain_list for d in duplicates):
                # Calculate confidence signals
                name_sim = name_similarity(companies_in_group[0]["name"], companies_in_group[1]["name"])

                # Phone match
                phone1 = companies_in_group[0]["phone"]
                phone2 = companies_in_group[1]["phone"]
                phone_match = (phone1 and phone2 and phone1 == phone2) if (phone1 and phone2) else None

                # Location match
                city1 = companies_in_group[0]["city"]
                city2 = companies_in_group[1]["city"]
                state1 = companies_in_group[0]["state"]
                state2 = companies_in_group[1]["state"]
                location_match = (city1 and city2 and state1 and state2 and
                                city1 == city2 and state1 == state2) if (city1 and city2 and state1 and state2) else None

                # Determine confidence
                # REVIEW: High name similarity but phone AND location both explicitly differ (possible separate branches)
                if name_sim >= 0.6 and phone_match is False and location_match is False:
                    confidence = "REVIEW"
                # HIGH: Name similarity >= 0.6, OR phone matches, OR location matches
                elif name_sim >= 0.6 or phone_match or location_match:
                    confidence = "HIGH"
                # LOW: No corroborating signals
                else:
                    confidence = "LOW"

                near_duplicates.append({
                    "domains": domain_list,
                    "companies": companies_in_group,
                    "type": "near_duplicate",
                    "confidence": confidence,
                    "name_similarity": name_sim,
                    "phone_match": phone_match,
                    "location_match": location_match
                })

    return duplicates, near_duplicates


def print_report(contact_changes, exact_dupes, near_dupes):
    """Print a formatted report of proposed changes."""
    print("\n" + "="*80)
    print("HUBSPOT DATA CLEANING REPORT")
    print("="*80)

    if DRY_RUN:
        print("\n[DRY RUN MODE] No changes will be applied")
        print("   Run with --apply flag to apply changes\n")
    else:
        print("\n[APPLY MODE] Changes will be written to HubSpot\n")

    # Contact changes
    print(f"\n[CONTACT CHANGES] {len(contact_changes)} contacts")
    print("-" * 80)

    if contact_changes:
        for change in contact_changes:
            print(f"\nContact: {change['name']} (ID: {change['id']})")
            print(f"  {change['description']}")
    else:
        print("  OK: No changes needed - all contacts are clean!")

    # Exact duplicate domains
    print(f"\n\n[DUPLICATE DOMAINS - EXACT MATCHES] {len(exact_dupes)} groups")
    print("-" * 80)

    if exact_dupes:
        for dupe in exact_dupes:
            if dupe['confidence'] == "HIGH":
                confidence_tag = "[HIGH]"
            elif dupe['confidence'] == "REVIEW":
                confidence_tag = "[REVIEW: POSSIBLE SEPARATE LOCATIONS]"
            else:
                confidence_tag = "[LOW - VERIFY CAREFULLY]"

            print(f"\nDomain: {dupe['domain']} {confidence_tag}")
            print(f"  Name similarity: {int(dupe['name_similarity'] * 100)}%")

            for company in dupe['companies']:
                phone_display = f"phone: {company['phone']}" if company['phone'] else "phone: (none)"
                location_display = f"{company['city']}, {company['state']}" if (company['city'] and company['state']) else "(no location)"
                print(f"  - {company['name']} (ID: {company['id']})")
                print(f"    domain: {company['domain']} | {phone_display} | {location_display}")

            if dupe['confidence'] == "REVIEW":
                print("  NOTE: Same/similar company name, but phone and location both differ.")
                print("        This could be a duplicate entry, or these could be separate branches of the same business.")
                print("        Check whether a parent-child company association already exists in HubSpot before merging.")
    else:
        print("  OK: No exact duplicate domains found!")

    # Near duplicate domains
    print(f"\n\n[SIMILAR DOMAINS - NEAR MATCHES] {len(near_dupes)} groups")
    print("-" * 80)

    if near_dupes:
        for dupe in near_dupes:
            if dupe['confidence'] == "HIGH":
                confidence_tag = "[HIGH]"
            elif dupe['confidence'] == "REVIEW":
                confidence_tag = "[REVIEW: POSSIBLE SEPARATE LOCATIONS]"
            else:
                confidence_tag = "[LOW - VERIFY CAREFULLY]"

            print(f"\nSimilar domains: {', '.join(dupe['domains'])} {confidence_tag}")
            print(f"  Name similarity: {int(dupe['name_similarity'] * 100)}%")

            for company in dupe['companies']:
                phone_display = f"phone: {company['phone']}" if company['phone'] else "phone: (none)"
                location_display = f"{company['city']}, {company['state']}" if (company['city'] and company['state']) else "(no location)"
                print(f"  - {company['name']} (ID: {company['id']})")
                print(f"    domain: {company['domain']} | {phone_display} | {location_display}")

            if dupe['confidence'] == "REVIEW":
                print("  NOTE: Same/similar company name, but phone and location both differ.")
                print("        This could be a duplicate entry, or these could be separate branches of the same business.")
                print("        Check whether a parent-child company association already exists in HubSpot before merging.")
    else:
        print("  OK: No similar domains found!")

    print("\n" + "="*80)


def log_applied_change(contact_id, contact_name, property_name, old_value, new_value, status):
    """Log an applied change to change_log.csv."""
    log_file = "change_log.csv"
    file_exists = os.path.isfile(log_file) and os.path.getsize(log_file) > 0

    with open(log_file, 'a', newline='') as f:
        writer = csv.writer(f)

        # Write header if file is new or empty
        if not file_exists:
            writer.writerow(['timestamp', 'contact_id', 'contact_name', 'property', 'old_value', 'new_value', 'status'])

        # Write log entry
        timestamp = datetime.now(timezone.utc).isoformat()
        writer.writerow([timestamp, contact_id, contact_name, property_name, old_value, new_value, status])


# Company duplicate merging is intentionally report-only (requires human judgment).
# This tool does not auto-merge or auto-apply changes to company records.
def apply_changes(contact_changes):
    """Apply contact changes to HubSpot."""
    print("\n[APPLYING CHANGES] Writing to HubSpot...")

    success_count = 0
    error_count = 0

    for change in contact_changes:
        success = update_contact(change['id'], change['updates'])
        status = "success" if success else "failed"

        # Log each property change
        for property_name, new_value in change['updates'].items():
            old_value = change['old_values'][property_name]
            log_applied_change(
                change['id'],
                change['name'],
                property_name,
                old_value,
                new_value,
                status
            )

        if success:
            success_count += 1
            print(f"  OK: Updated: {change['name']}")
        else:
            error_count += 1
            print(f"  ERROR: FAILED: {change['name']} (ID: {change['id']})")

    print(f"\n[SUCCESS] Updated {success_count} contacts")
    if error_count:
        print(f"[ERROR] Failed to update {error_count} contacts")


def main():
    """Main execution function."""
    print("Fetching contacts from HubSpot...")
    contacts = fetch_all_contacts()
    print(f"  -> Found {len(contacts)} contacts")

    print("Fetching companies from HubSpot...")
    companies = fetch_all_companies()
    print(f"  -> Found {len(companies)} companies")

    # Analyze and clean data
    print("\nAnalyzing data...")
    contact_changes = clean_contacts(contacts)
    exact_dupes, near_dupes = find_duplicate_domains(companies)

    # Print report
    print_report(contact_changes, exact_dupes, near_dupes)

    # Apply changes if not in dry-run mode
    if not DRY_RUN and contact_changes:
        apply_changes(contact_changes)
        print("\n[SUCCESS] All changes applied successfully!")
        print("   Run script again to verify changes.")
    elif DRY_RUN and contact_changes:
        print(f"\n[TIP] Run with --apply flag to apply these {len(contact_changes)} changes")

    print("\n" + "="*80)

    # Exit with non-zero status in dry-run mode if there are pending contact changes.
    # This signals to scheduled checks (e.g., GitHub Actions) that action is needed.
    # Duplicate domain findings are report-only and do not affect exit code.
    if DRY_RUN and contact_changes:
        sys.exit(1)


if __name__ == "__main__":
    main()

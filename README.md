# HubSpot Data Cleaning Tool

A Python script that finds and fixes common CRM data-quality problems in HubSpot: inconsistent name casing, inconsistent phone number formats, and duplicate or near-duplicate company records. Built against a HubSpot developer test portal seeded with data modeled on a fictional HVAC field-service SaaS business (FieldPilot).

## Why this exists

Messy CRM data is one of the most common, unglamorous problems in RevOps — inconsistent name casing looks unprofessional in emails, malformed phone numbers break dialer/SMS integrations, and duplicate company records split a single account's history across two CRM entries. This tool automates detection (and, for names/phones, correction) of those issues, with a deliberate emphasis on **not making things worse** — see the safety design and known limitations below.

## What it does

- **Title-cases contact names** — handles apostrophes (`O'Brien`), hyphens (`Mary-Jane`), multi-word names, and the `Mc`/`Mac` prefix pattern (`McDonald`, `MacArthur`) as a special case, since standard title-casing breaks on all of these.
- **Standardizes phone numbers** to `+1XXXXXXXXXX` format for US numbers (10 digits, or 11 digits starting with `1`). Numbers that don't match a recognized US format are left unchanged rather than guessed at.
- **Flags duplicate company domains** — both exact matches and a specific class of near-duplicates (domains that differ only by hyphens/underscores, e.g. `coolbreezehvac.com` vs. `coolbreeze-hvac.com`).

## Safety design

The script defaults to **dry-run mode** — it always fetches data and prints a full report of what it *would* change, and never writes anything to HubSpot unless you explicitly pass `--apply`.

```
python clean_hubspot_data.py          # Dry-run: shows the report, changes nothing
python clean_hubspot_data.py --apply  # Writes the reported changes to HubSpot
```

Company duplicate detection is **report-only, by design** — the script never auto-merges or auto-edits company records. Merging two CRM records is a judgment call (which record is "correct," what happens to associated deals/tickets) that shouldn't be automated without a human deciding.

## Known limitations (documented, not accidental)

Being upfront about what this tool doesn't do is as important as what it does:

- **The `Mc`/`Mac` fix is a heuristic, not a complete solution.** It correctly turns `mcdonald` into `McDonald`, but it will also incorrectly turn a genuinely different name like `macy` into `MacY`, since the script can't distinguish "Mac-prefixed compound surname" from "a name that happens to start with those three letters." A production version would need a verified name-lookup list rather than a pure rule, and this tradeoff was confirmed intentionally against real test data before shipping.
- **Phone standardization is US-only.** International numbers are returned unchanged rather than mis-formatted.
- **Near-duplicate domain detection only catches one specific pattern** — hyphen/underscore differences. It will not catch typos, different TLDs, or abbreviated company names on different domains.
- **Parenthetical suffixes in names are preserved untouched** — this was added specifically after testing against HubSpot's own default sample contacts, which store text like `(Sample Contact)` as part of the lastname field. An earlier version of the title-casing logic incorrectly re-cased that suffix; the fix isolates and skips anything from an opening parenthesis onward.

## Setup

1. Clone this repo and create a virtual environment:
   ```
   python -m venv venv
   venv\Scripts\activate        # Windows
   source venv/bin/activate     # Mac/Linux
   ```
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create a `.env` file in the project root with a HubSpot Service Key (Settings → Integrations → Service Keys in your HubSpot portal), scoped to:
   - `crm.objects.contacts.read` / `crm.objects.contacts.write`
   - `crm.objects.companies.read` / `crm.objects.companies.write`
   ```
   HUBSPOT_TOKEN=your_token_here
   ```
   `.env` is excluded via `.gitignore` and should never be committed.

## Usage

```
python clean_hubspot_data.py          # Review proposed changes
python clean_hubspot_data.py --apply  # Apply them
```

Re-running after `--apply` should show zero contact changes remaining (the fixes are idempotent) — duplicate domain groups will continue to appear on every run, since that detection is intentionally report-only rather than something the tool marks "resolved."

## Tech stack

Python, `requests`, `python-dotenv`, HubSpot CRM API v3, authenticated via HubSpot Service Keys.

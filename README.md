# HubSpot Data Cleaning Tool

A Python script that finds and fixes common CRM data-quality problems in HubSpot: inconsistent name casing, inconsistent phone number formats, and duplicate or near-duplicate company records. Built against a HubSpot developer test portal seeded with data modeled on a fictional HVAC field-service SaaS business (FieldPilot).

## Why this exists

Messy CRM data is one of the most common, unglamorous problems in RevOps — inconsistent name casing looks unprofessional in emails, malformed phone numbers break dialer/SMS integrations, and duplicate company records split a single account's history across two CRM entries. This tool automates detection (and, for names/phones, correction) of those issues, with a deliberate emphasis on **not making things worse** — see the safety design and known limitations below.

## What it does

- **Title-cases contact names** — handles apostrophes (`O'Brien`), hyphens (`Mary-Jane`), multi-word names, and the `Mc`/`Mac` prefix pattern (`McDonald`, `MacArthur`) as a special case, since standard title-casing breaks on all of these.
- **Standardizes phone numbers** to `+1XXXXXXXXXX` format for US numbers (10 digits, or 11 digits starting with `1`). Numbers that don't match a recognized US format are left unchanged rather than guessed at.
- **Flags duplicate company domains with confidence scoring** — both exact matches and a specific class of near-duplicates (domains that differ only by hyphens/underscores, e.g. `coolbreezehvac.com` vs. `coolbreeze-hvac.com`). Each flagged pair is labeled **HIGH**, **REVIEW**, or **LOW** confidence based on corroborating signals (company name similarity, matching phone, matching city/state) — see "Duplicate confidence scoring" below.
- **Keeps an independent audit log** of every write actually applied to HubSpot — see "Audit log" below.
- **Runs on a schedule via GitHub Actions**, checking for new data-quality issues automatically — see "Automation" below.

## Safety design

The script defaults to **dry-run mode** — it always fetches data and prints a full report of what it *would* change, and never writes anything to HubSpot unless you explicitly pass `--apply`.

```
python clean_hubspot_data.py          # Dry-run: shows the report, changes nothing
python clean_hubspot_data.py --apply  # Writes the reported changes to HubSpot
```

Company duplicate detection is **report-only, by design** — the script never auto-merges or auto-edits company records. Merging two CRM records is a judgment call (which record is "correct," what happens to associated deals/tickets) that shouldn't be automated without a human deciding.

## Duplicate confidence scoring

A shared or similar domain alone can't tell a genuine duplicate apart from two unrelated companies that happen to have similar-shaped domains — so every flagged pair is scored using three signals: normalized company-name similarity (business suffixes like LLC/Inc/Services are stripped before comparing), whether phone numbers match, and whether city+state both match. Each signal is only counted when both companies actually have a value for it — a missing field is treated as "unknown," never as a mismatch.

- **HIGH** — at least one strong corroborating signal (name similarity ≥60%, or matching phone, or matching city/state). Safe to review with confidence this is likely a real duplicate.
- **REVIEW** — name similarity is high, but phone *and* location both explicitly conflict (both companies have values, and they differ). This is a deliberately distinct case: it could be a genuine duplicate, or it could be two legitimately separate branch locations of the same business that happen to share a name. The tool can't tell these apart from data alone — check whether a parent-child company association already exists in HubSpot (the native feature for exactly this scenario) before assuming either way.
- **LOW** — no corroborating signal at all beyond the domain shape. Likely two unrelated companies whose domains coincidentally look similar.

## Audit log

Every write actually applied via `--apply` is recorded to `change_log.csv` — one row per property changed, with a timestamp, the contact, the old and new values, and whether that specific write succeeded or failed. This exists as an audit trail independent of HubSpot's own property history, which caps at a limited number of revisions per property. Note what this log does *not* do: it can't tell you whether an applied fix was actually *correct* (see the Mc/Mac limitation below) — it only tells you exactly what changed and when, so a bad fix is at least recoverable rather than invisible. `change_log.csv` is excluded from version control via `.gitignore`, since it's runtime data, not source code.

## Automation

A GitHub Actions workflow (`.github/workflows/data-cleaning-check.yml`) runs the script in **dry-run mode only**, on a weekly schedule plus a manual trigger. It deliberately never runs `--apply` automatically — the whole point of the dry-run/apply split is a human reviewing proposed changes before anything is written, and automating that away would remove the one safety checkpoint the tool is built around. The scheduled check exits with a failure status specifically when there are new *contact* changes pending — duplicate domain groups are excluded from that condition, since they're permanent, report-only findings that never resolve through this tool alone; including them would make the check fail forever and train you to ignore it.

## Known limitations (documented, not accidental)

Being upfront about what this tool doesn't do is as important as what it does:

- **The `Mc`/`Mac` fix is a heuristic, not a complete solution.** It correctly turns `mcdonald` into `McDonald`, but it will also incorrectly turn a genuinely different name like `macy` into `MacY`, since the script can't distinguish "Mac-prefixed compound surname" from "a name that happens to start with those three letters." A production version would need a verified name-lookup list rather than a pure rule, and this tradeoff was confirmed intentionally against real test data before shipping. Because the fix is idempotent, an incorrectly-applied name doesn't get flagged again on subsequent runs — the audit log (above) is what makes it recoverable, not the detection logic.
- **Phone standardization is US-only.** International numbers are returned unchanged rather than mis-formatted.
- **Near-duplicate domain detection only catches one specific pattern** — hyphen/underscore differences. It will not catch typos, different TLDs, or abbreviated company names on different domains.
- **Confidence scoring is a heuristic aid for human review, not a verdict.** The REVIEW tier in particular is a genuinely ambiguous case by design — the tool surfaces the right question (possible duplicate vs. possible separate branch) rather than guessing an answer it can't actually know.
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

Python, `requests`, `python-dotenv`, `difflib` (name similarity), HubSpot CRM API v3 (authenticated via HubSpot Service Keys), GitHub Actions (scheduled dry-run checks).

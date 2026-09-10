# FieldPilot Schema — Setup Checklist

Fictional company: **FieldPilot** — subscription SaaS for HVAC/plumbing/electrical field service businesses, priced per technician. This is the shared schema all three portfolio tools (data cleaning, handoff, NRR/cohort) will read from and write to in your HubSpot test portal.

---

## 1. Company object — custom properties

| Property name | Type | Options / Notes | Used by |
|---|---|---|---|
| `crew_size` | Number | Technicians on the account | Handoff (routing tier), NRR (expansion signal) |
| `plan_tier` | Dropdown | Solo / Small Crew / Multi-Crew / Enterprise | Handoff (routing tier) |
| `monthly_recurring_revenue` | Number | MRR = per-tech price × crew_size | NRR tool |
| `payment_processor_connected` | Boolean (checkbox) | Onboarding milestone | Health-score / stall logic, if you add that project back in |
| `accounting_sync_connected` | Boolean (checkbox) | QuickBooks-style integration milestone | Same as above |
| `onboarding_status` | Dropdown | Not Started / In Progress / Live / Stalled | Handoff tool sets this on ticket creation |
| `customer_since` | Date | Date the account first went Closed Won | **NRR tool's cohort anchor** — every account groups by the month this falls in |

---

## 2. Deal pipeline — "Sales Pipeline"

**Stages:** New Lead → Demo Scheduled → Proposal Sent → Closed Won / Closed Lost
(Default HubSpot pipeline stages work fine — no need to build a custom pipeline unless you want to.)

**Custom deal properties:**

| Property name | Type | Options / Notes | Used by |
|---|---|---|---|
| `deal_type` | Dropdown | New Business / Expansion / Downgrade / Renewal | **NRR tool depends on this** to separate new revenue from expansion/contraction |
| `crew_size_requested` | Number | Feeds routing logic | Handoff tool (assigns onboarding specialist by size) |
| `mrr_amount` | Number | Revenue this specific deal represents | Handoff tool (copies to Company), NRR tool |

> **`mrr_amount` vs. `monthly_recurring_revenue`:** `mrr_amount` is the *delta* this one deal represents — for a New Business deal, that's the account's starting MRR; for an Expansion or Downgrade deal, it's just the incremental change, not the account's new total. `monthly_recurring_revenue` (Company property, above) is the account's *current running total*. The NRR tool needs the per-deal deltas, not just the running total, to calculate expansion/contraction correctly.

**Trigger point:** the handoff tool fires when a deal with `deal_type = New Business` reaches **Closed Won**.

---

## 3. Ticket pipeline — "Onboarding" (Service Hub)

**Stages:** Kickoff Scheduled → Payment Processor Connected → Accounting Synced → Live

No custom ticket properties needed to start — stage alone tracks onboarding progress. The handoff tool creates one ticket per new Closed Won deal, in this pipeline, at the first stage.

---

## 4. Associations (standard, no setup needed)

- Deals → Companies (default association)
- Tickets → Companies (default association)

---

## 5. Which tool touches what

- **Data cleaning tool** — operates on Contact/Company records (name casing, phone formatting, duplicate domains). Deliberately **doesn't depend on any property above** — keep it config-driven so it stays reusable.
- **Handoff tool** — trigger: Deal `deal_type = New Business` → Closed Won. Action: create Ticket in Onboarding pipeline, copy `crew_size` / `plan_tier` / `mrr_amount` from Deal to Company, assign owner by crew-size tier.
- **NRR tool** — reads Company (`customer_since` for cohort grouping) + Deal (`deal_type`, `mrr_amount`) via API, read-only, no workflow needed.

---

## Build order (matches earlier discussion)

1. Data cleaning tool
2. Handoff tool (needs the Deal/Ticket pipelines above)
3. NRR tool (needs `customer_since` + several months of `deal_type`-tagged deals — this is what the synthetic data generator would seed)

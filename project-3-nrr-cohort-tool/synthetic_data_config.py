"""
Tunable knobs for generate_synthetic_data.py.

Everything in this file corresponds to a value the spec (data-generator-spec.md)
explicitly marks "defaults -- adjust freely." Fixed mechanics (deal_type mapping,
batch chunking, churn-state handling) live in generate_synthetic_data.py itself,
not here -- this file should only ever need edits to change simulation shape,
never simulation logic.
"""

# --- Time span & cohort composition ---------------------------------------

# How many months of cohort history to simulate, ending at "today" (the
# present-day cutoff). Month 0 is the oldest cohort.
COHORT_MONTHS = 18

# Average number of new companies signed up per calendar month.
COMPANIES_PER_MONTH = 10

# Tier mix for new signups. Must sum to 1.0.
TIER_WEIGHTS = {
    "Solo": 0.35,
    "Small Crew": 0.35,
    "Multi-Crew": 0.20,
    "Enterprise": 0.10,
}

# Renewal cadence per tier, in months (1 = monthly, 12 = annual).
TIER_CADENCE_MONTHS = {
    "Solo": 1,
    "Small Crew": 1,
    "Multi-Crew": 12,
    "Enterprise": 12,
}

# FieldPilot is priced per technician. MRR is always seats * this rate --
# starting_mrr and every Expansion/Downgrade mrr_amount derive from this,
# not from separately invented ranges.
PRICE_PER_TECHNICIAN = 50

# crew_size range per tier, used both to draw a plausible new-company profile
# at signup and as the tier boundaries for migration (see
# generate_synthetic_data.py: crossing into another tier's range on an
# Expansion/Downgrade updates plan_tier and unconditionally restarts the
# renewal clock on the new tier's cadence from the migration date -- even
# between two tiers sharing a cadence, e.g. Solo<->Small Crew -- rather than
# only resetting when the cadence itself actually changes).
# (min, max) inclusive, uniform int draw for signup.
# starting_mrr is derived: crew_size * PRICE_PER_TECHNICIAN.
TIER_CREW_SIZE_RANGES = {
    "Solo": (1, 1),
    "Small Crew": (2, 5),
    "Multi-Crew": (6, 20),
    "Enterprise": (21, 60),  # upper bound is a soft ceiling for seat-delta draws, not a hard cap
}

# --- Renewal decision-point probabilities ----------------------------------
# Each table must sum to 1.0. Rolled once per renewal decision point, using
# the table for the company's *current* tier's cadence (which can change
# mid-simulation via migration).

ANNUAL_RENEWAL_PROBABILITIES = {
    "flat": 0.45,
    "expansion": 0.35,
    "downgrade": 0.10,
    "churn": 0.10,
}

MONTHLY_RENEWAL_PROBABILITIES = {
    "flat": 0.92,
    "expansion": 0.04,
    "downgrade": 0.025,
    "churn": 0.015,
}

# Whole-seat delta range for an Expansion/Downgrade event, uniform int draw.
# crew_size_requested (Deal property, reused from Project #2's schema) stores
# this as a positive magnitude regardless of direction -- deal_type carries
# the direction. Neither range is bounded by the company's current crew_size
# at draw time -- there's no hard upper cap on expansion (Enterprise's range
# top in TIER_CREW_SIZE_RANGES is a soft ceiling only), and a downgrade delta
# is drawn freely too: if crew_size - delta would be <= 0, that decision
# point resolves to a Churn deal for the full remaining crew_size/MRR instead
# of a capped Downgrade (crew_size floor is 1, never 0). Solo-tier companies
# (crew_size already 1) therefore always resolve a downgrade roll to Churn --
# this falls out of the floor rule with no special case needed.
EXPANSION_SEATS_RANGE = (1, 8)
DOWNGRADE_SEATS_RANGE = (1, 4)

# --- Reactivation modeling ---------------------------------------------------

# Of companies that churn, the split of what happens next. Must sum to 1.0.
#
# NOTE: after_grace_window's realized rate in a given run will land below
# this configured value, and that's expected, not a bug. A churn needs the
# full REACTIVATION_GRACE_WINDOW_MONTHS to elapse *and* still leave enough
# runway before the present-day cutoff for the win-back signup itself to
# land on or before today -- churns near the end of the simulated window
# don't have that runway, so their after_grace_window roll is silently
# absorbed into "no time left" rather than fabricating an event past today.
# generate_synthetic_data.py's run_simulation() prints the actual realized
# counts so this is visible whenever the numbers are looked at, not just
# documented here.
REACTIVATION_SPLIT = {
    "in_grace_window": 0.20,   # same company record, same cohort
    "after_grace_window": 0.10,  # new company record, new cohort (re-rolled)
    "never": 0.70,
}

# Grace window length in months, used both to bound the in-window reactivation
# timing roll and as the churned_date comparison point for that boundary.
REACTIVATION_GRACE_WINDOW_MONTHS = 6

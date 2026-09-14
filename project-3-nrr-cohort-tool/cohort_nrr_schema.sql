-- FieldPilot Project #3: NRR / Cohort Analytics Engine
-- Output table -- this is what Project #4 (the reporting dashboard) reads from.
--
-- GRAIN: one row per (cohort_month, elapsed_month) pair.
--   A cohort = every company sharing the same customer_since month.
--   elapsed_month counts months since THAT cohort's own start (0, 1, 2...),
--   so "elapsed_month = 3" means a different real month for every cohort.
--   calendar_month is stored alongside it for questions elapsed_month can't
--   answer (seasonality, a company-wide event hitting everyone at once,
--   finance/board reporting that thinks in real calendar terms).

CREATE TABLE cohort_nrr (

    -- IDENTITY: which cohort this row belongs to.
    cohort_month                DATE          NOT NULL,  -- first-of-month customer_since

    -- TENURE: how far into this cohort's own lifecycle this row is.
    elapsed_month                INTEGER       NOT NULL CHECK (elapsed_month >= 0),

    -- REAL TIME: cohort_month + elapsed_month, stored directly rather than
    -- computed on every query.
    calendar_month               DATE          NOT NULL,

    -- ANCHOR: fixed for the life of the cohort. Every row for this cohort
    -- divides by this same number -- never a rolling "last month" figure,
    -- so a bad month can't quietly undo itself just by comparing against
    -- an already-lower base. Same-calendar-month follow-on deals (e.g. an
    -- upsell two weeks after signup) are folded into this figure rather
    -- than tracked separately.
    starting_mrr                 NUMERIC(12,2) NOT NULL,

    -- PERIOD FLOWS: what happened in THIS elapsed_month specifically.
    -- Useful for explaining "why did the number move this month" --
    -- NOT used directly in the NRR/GRR formula below.
    expansion_mrr                NUMERIC(12,2) NOT NULL DEFAULT 0,
    reactivation_mrr             NUMERIC(12,2) NOT NULL DEFAULT 0,
    contraction_mrr              NUMERIC(12,2) NOT NULL DEFAULT 0,  -- stored positive
    churn_mrr                    NUMERIC(12,2) NOT NULL DEFAULT 0,  -- stored positive

    -- CUMULATIVE FLOWS: running totals from elapsed_month 0 through this
    -- row. These are what the NRR/GRR formulas actually use.
    cumulative_expansion_mrr     NUMERIC(12,2) NOT NULL DEFAULT 0,
    cumulative_reactivation_mrr  NUMERIC(12,2) NOT NULL DEFAULT 0,
    cumulative_contraction_mrr   NUMERIC(12,2) NOT NULL DEFAULT 0,
    cumulative_churn_mrr         NUMERIC(12,2) NOT NULL DEFAULT 0,

    -- CURRENT STATE: the cohort's actual live MRR as of this row --
    -- starting_mrr adjusted by every cumulative bucket to date.
    -- Also what the 6-month dormancy rule watches: six consecutive rows
    -- at 0 means this cohort's rows stop generating after that point,
    -- until/unless a reactivation within the grace window revives it.
    current_mrr                  NUMERIC(12,2) NOT NULL,

    -- HEADLINE METRICS: computed once at write time, so Project #4 never
    -- has to re-derive them. Both fixed at 100.00 at elapsed_month = 0
    -- (nothing has happened yet, but the row still needs a plottable value).
    nrr_pct                      NUMERIC(6,2)  NOT NULL,  -- current_mrr / starting_mrr * 100
    grr_pct                      NUMERIC(6,2)  NOT NULL,  -- (starting_mrr - cumulative_contraction_mrr - cumulative_churn_mrr) / starting_mrr * 100

    -- BOOKKEEPING
    computed_at                  TIMESTAMPTZ   NOT NULL DEFAULT now(),

    PRIMARY KEY (cohort_month, elapsed_month)
);

-- Reactivation-after-grace-period rule (application logic, not enforced by
-- this table): if a company reactivates within 6 months of its cohort's
-- current_mrr first hitting 0, it returns to THIS cohort (customer_since
-- unchanged). If it reactivates after that grace window has passed (this
-- cohort's rows have already stopped generating), it's treated as a new
-- signup: fresh customer_since, new cohort_month, and the deal itself is
-- classified New Business rather than Reactivation.

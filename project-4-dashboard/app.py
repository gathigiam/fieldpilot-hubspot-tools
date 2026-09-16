"""
FieldPilot Project #4: Reporting Dashboard.

Reads the cohort_nrr table (populated nightly by Project #3's
compute_cohort_nrr.py) and renders a single-page dashboard: headline
blended NRR/GRR, a cohort retention heatmap, per-cohort retention
curves, and a monthly MRR movement breakdown.

Connects using the read-only fieldpilot_dashboard Postgres role --
this app never writes to the database.
"""

import os
from decimal import Decimal

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from flask import Flask, render_template

load_dotenv()

app = Flask(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def nrr_color(pct):
    """Map an NRR percentage to a (background, text) hex color pair for
    the heatmap. Red (churn-heavy) -> amber -> green (expanding),
    calibrated around 100% (breakeven) as a middle stop. This is a
    display concern only -- the numeric value is always shown too.
    """
    if pct is None:
        return "#1B2027", "#5A6270"

    pct = float(pct)
    stops = [
        (60.0, (196, 76, 60)),
        (85.0, (201, 151, 58)),
        (100.0, (167, 178, 74)),
        (120.0, (78, 143, 98)),
    ]
    if pct <= stops[0][0]:
        r, g, b = stops[0][1]
    elif pct >= stops[-1][0]:
        r, g, b = stops[-1][1]
    else:
        r, g, b = stops[-1][1]
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t0 <= pct <= t1:
                frac = (pct - t0) / (t1 - t0)
                r = round(c0[0] + (c1[0] - c0[0]) * frac)
                g = round(c0[1] + (c1[1] - c0[1]) * frac)
                b = round(c0[2] + (c1[2] - c0[2]) * frac)
                break

    text = "#12161C" if pct >= 85 else "#F3EFE8"
    return f"#{r:02x}{g:02x}{b:02x}", text


@app.route("/")
def dashboard():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # --- Headline: dollar-weighted blended NRR/GRR at the latest snapshot month ---
            cur.execute("""
                SELECT
                    SUM(current_mrr)                AS total_current_mrr,
                    SUM(starting_mrr)                AS total_starting_mrr,
                    SUM(cumulative_contraction_mrr)  AS total_contraction,
                    SUM(cumulative_churn_mrr)        AS total_churn,
                    COUNT(*)                         AS active_cohorts,
                    MAX(calendar_month)              AS latest_month
                FROM cohort_nrr
                WHERE calendar_month = (SELECT MAX(calendar_month) FROM cohort_nrr)
            """)
            row = cur.fetchone() or {}

            total_current = row.get("total_current_mrr") or Decimal("0")
            total_starting = row.get("total_starting_mrr") or Decimal("0")
            total_contraction = row.get("total_contraction") or Decimal("0")
            total_churn = row.get("total_churn") or Decimal("0")

            blended_nrr = float(total_current / total_starting * 100) if total_starting else 0.0
            blended_grr_raw = (
                float((total_starting - total_contraction - total_churn) / total_starting * 100)
                if total_starting else 0.0
            )

            latest_month = row.get("latest_month")
            headline = {
                "blended_nrr": round(blended_nrr, 1),
                "blended_grr": round(max(0.0, blended_grr_raw), 1),
                "active_cohorts": row.get("active_cohorts") or 0,
                "total_current_mrr": float(total_current),
                "latest_month": latest_month.strftime("%B %Y") if latest_month else "\u2014",
            }

            # --- Retention curves + heatmap: both built from the same per-row data ---
            cur.execute("""
                SELECT cohort_month, elapsed_month, nrr_pct
                FROM cohort_nrr
                ORDER BY cohort_month, elapsed_month
            """)
            rows = cur.fetchall()

            cohorts = sorted({r["cohort_month"] for r in rows})
            max_elapsed = max((r["elapsed_month"] for r in rows), default=0)

            grid = {c: {} for c in cohorts}
            for r in rows:
                grid[r["cohort_month"]][r["elapsed_month"]] = float(r["nrr_pct"])

            curve_series = []
            heatmap_rows = []
            for c in cohorts:
                label = c.strftime("%b %Y")
                points = [grid[c].get(m) for m in range(max_elapsed + 1)]
                curve_series.append({"label": label, "data": points})

                cells = []
                for m in range(max_elapsed + 1):
                    pct = grid[c].get(m)
                    bg, text_color = nrr_color(pct)
                    cells.append({
                        "value": f"{pct:.0f}%" if pct is not None else "",
                        "bg": bg,
                        "text": text_color,
                    })
                heatmap_rows.append({"label": label, "cells": cells})

            # --- MRR movement breakdown, by calendar month ---
            cur.execute("""
                SELECT calendar_month,
                       SUM(expansion_mrr)    AS expansion,
                       SUM(reactivation_mrr) AS reactivation,
                       SUM(contraction_mrr)  AS contraction,
                       SUM(churn_mrr)        AS churn
                FROM cohort_nrr
                GROUP BY calendar_month
                ORDER BY calendar_month
            """)
            movement_rows = cur.fetchall()
            movement = {
                "labels": [r["calendar_month"].strftime("%b %Y") for r in movement_rows],
                "expansion": [float(r["expansion"]) for r in movement_rows],
                "reactivation": [float(r["reactivation"]) for r in movement_rows],
                "contraction": [-float(r["contraction"]) for r in movement_rows],
                "churn": [-float(r["churn"]) for r in movement_rows],
            }
    finally:
        conn.close()

    return render_template(
        "index.html",
        headline=headline,
        curve_series=curve_series,
        heatmap_rows=heatmap_rows,
        movement=movement,
    )


if __name__ == "__main__":
    # Local/dev only -- production serving is via gunicorn (see systemd unit).
    app.run(host="127.0.0.1", port=5004, debug=False)

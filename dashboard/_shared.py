"""Shared data access and chart vocabulary for the dashboard.

Read-only against `data/jobs.db` (PLAN §9). Nothing here writes — the dashboard is a
view over what the pipeline produced, and Streamlit Cloud runs it from a repo checkout.

Colour follows the *entity*, never its rank: every taxonomy slug owns a fixed palette
slot, so filtering the chart down to three categories never repaints the survivors. The
slot order is the validated one (see dataviz skill); do not reorder it casually — the
ordering is the colourblind-safety mechanism, not decoration.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.db"

# --- Palette -----------------------------------------------------------------------
# Categorical slots 1-7, validated light and dark (worst adjacent CVD ΔE 9.1 / 8.4).
_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7")
_DARK = ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9")

#: Fixed slug -> slot assignment, in taxonomy order. Tier-1 categories only.
TIER1_ORDER = (
    "data_scientist",
    "ai_engineer",
    "ml_engineer",
    "agentic_engineer",
    "forward_deployed_engineer",
    "engineering_analyst",
    "data_analyst",
)

#: The tier-2 baseline is deliberately NOT a categorical slot — it is context, not a
#: peer series, so it renders in muted grey behind the tier-1 lines (PLAN §9).
BASELINE_COLOR = "#898781"
OTHER_COLOR = "#c3c2b7"

MUTED_INK = "#898781"
GRIDLINE = "#e1e0d9"


def category_colors(dark: bool = False) -> dict[str, str]:
    """Slug -> hex. Stable across filters and across pages."""
    ramp = _DARK if dark else _LIGHT
    colors = dict(zip(TIER1_ORDER, ramp, strict=True))
    colors["software_developer"] = BASELINE_COLOR
    colors["other"] = OTHER_COLOR
    return colors


def color_scale(slugs: list[str], dark: bool = False):
    """An Altair scale pinned to the fixed assignment, so identity survives filtering."""
    import altair as alt

    mapping = category_colors(dark)
    domain = [s for s in (*TIER1_ORDER, "software_developer", "other") if s in slugs]
    return alt.Scale(domain=domain, range=[mapping[s] for s in domain])


def label_for(slug: str) -> str:
    """Human-readable category label, resolved from the taxonomy definition."""
    from tideline.taxonomy import BY_SLUG

    category = BY_SLUG.get(slug)
    return category.label if category else slug.replace("_", " ").title()


# --- Data --------------------------------------------------------------------------


@st.cache_resource
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data(ttl=600)
def load_jobs() -> pd.DataFrame:
    """Jobs left-joined to classifications.

    A LEFT join, deliberately: before Phase 2 runs, `classifications` is empty and every
    category is null. Pages must degrade to "not classified yet" rather than showing an
    empty table that looks like no jobs exist.
    """
    df = pd.read_sql_query(
        """
        SELECT
            j.id, j.source, j.company, j.title, j.location_raw, j.country,
            j.subregion, j.workplace_type, j.is_remote,
            j.url, j.posted_at, j.first_seen, j.last_seen,
            j.is_active, j.closed_at,
            j.salary_min AS source_salary_min,
            j.salary_max AS source_salary_max,
            j.salary_currency AS source_salary_currency,
            c.category, c.tier, c.seniority, c.skills, c.confidence,
            c.salary_min AS llm_salary_min,
            c.salary_max AS llm_salary_max,
            c.salary_currency AS llm_salary_currency,
            c.visa_sponsorship
        FROM jobs j
        LEFT JOIN classifications c ON c.job_id = j.id
        """,
        _connect(),
    )
    for col in ("first_seen", "last_seen", "posted_at", "closed_at"):
        df[col] = pd.to_datetime(df[col], format="mixed", utc=True, errors="coerce")

    # Employer-stated compensation is ground truth; the model's extraction is the
    # fallback. Keeping both columns separate upstream lets us prefer correctly here.
    df["salary_min"] = df["source_salary_min"].fillna(df["llm_salary_min"])
    df["salary_max"] = df["source_salary_max"].fillna(df["llm_salary_max"])
    df["salary_currency"] = df["source_salary_currency"].fillna(df["llm_salary_currency"])
    return df


@st.cache_data(ttl=600)
def load_runs() -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT run_at, source, ok, jobs_seen, jobs_new, error FROM ingest_runs", _connect()
    )
    if not df.empty:
        df["run_at"] = pd.to_datetime(df["run_at"], format="mixed", utc=True, errors="coerce")
    return df


def is_classified(df: pd.DataFrame) -> bool:
    return bool(df["category"].notna().any())


def classification_notice() -> None:
    """Shown on pages that cannot function before Phase 2 has run."""
    st.info(
        "**Not classified yet.** This page reads the `classifications` table, which is "
        "populated by Phase 2. Run `python -m tideline.classify run` with "
        "`ANTHROPIC_API_KEY` set, then reload.",
        icon=":material/pending:",
    )


def page_header(title: str, subtitle: str) -> None:
    st.title(title)
    st.caption(subtitle)


def base_chart_props(chart):
    """Recessive grid and axes, per the chart-anatomy rules."""
    return (
        chart.configure_axis(
            grid=True,
            gridColor=GRIDLINE,
            gridWidth=1,
            domainColor="#c3c2b7",
            tickColor="#c3c2b7",
            labelColor=MUTED_INK,
            titleColor="#52514e",
            labelFontSize=12,
            titleFontSize=12,
        )
        .configure_view(strokeWidth=0)
        .configure_legend(labelColor="#52514e", titleColor="#52514e", labelFontSize=12)
    )

"""Page 1 — Job browser (the job layer, PLAN §9).

Entry point: `streamlit run dashboard/app.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import (  # noqa: E402
    TIER1_ORDER,
    is_classified,
    label_for,
    load_jobs,
    page_header,
)

st.set_page_config(page_title="tideline — jobs", page_icon="🌊", layout="wide")

page_header(
    "Job browser",
    "Postings from public ATS boards across Singapore, the US, Australia, and the Netherlands.",
)

df = load_jobs()
classified = is_classified(df)

# --- Scope -------------------------------------------------------------------------
# Home market and overseas are different decisions, not two values of one filter: an
# overseas role has to clear a relocation bar a Singapore role does not. Splitting the
# scope up front lets the overseas view carry its own, stricter controls.
OVERSEAS = ["US", "AU", "NL"]

scope = st.segmented_control(
    "Scope",
    ["Singapore", "Overseas", "All targets", "Everything"],
    default="Singapore",
    help="Overseas covers the US, Australia, and the Netherlands.",
)

if scope == "Singapore":
    picked_countries = ["SG"]
elif scope == "Overseas":
    picked_countries = OVERSEAS
elif scope == "All targets":
    picked_countries = ["SG", *OVERSEAS]
else:
    picked_countries = ["SG", *OVERSEAS, "OTHER"]

row1 = st.columns([1, 1.4, 1.4, 1.2])

with row1[0]:
    status = st.radio("Status", ["Active", "All", "Closed"], horizontal=True)

with row1[1]:
    if classified:
        options = [c for c in TIER1_ORDER if c in set(df["category"].dropna())]
        picked_categories = st.multiselect(
            "Role category", options, default=options, format_func=label_for
        )
    else:
        picked_categories = []
        st.multiselect("Role category", [], disabled=True, help="Available after Phase 2.")

with row1[2]:
    if classified:
        levels = ["intern", "junior", "mid", "senior", "staff_plus", "manager"]
        present = [lvl for lvl in levels if lvl in set(df["seniority"].dropna())]
        picked_levels = st.multiselect("Level", present, default=present)
    else:
        picked_levels = []
        st.multiselect("Level", [], disabled=True, help="Available after Phase 2.")

with row1[3]:
    keyword = st.text_input("Keyword", placeholder="title or company…")

row2 = st.columns([1.6, 1.6, 1.4, 1])

with row2[0]:
    # Three-way, not a boolean: hybrid still requires living in the city, so it is a
    # different answer from remote for a relocation decision.
    picked_workplace = st.multiselect(
        "Workplace",
        ["onsite", "hybrid", "remote", "unknown"],
        default=["onsite", "hybrid", "unknown"],
        help="Greenhouse publishes no workplace field, so many postings are 'unknown' — "
        "excluding them drops real roles, not just ambiguous ones.",
    )

with row2[1]:
    us_in_scope = "US" in picked_countries
    if us_in_scope:
        states = ["CA", "NY", "MA"]
        others = sorted(
            s for s in df[df["country"] == "US"]["subregion"].dropna().unique() if s not in states
        )
        picked_states = st.multiselect(
            "US states", [*states, *others], default=states, help="Applies to US postings only."
        )
    else:
        picked_states = []
        st.multiselect("US states", [], disabled=True, help="Select a scope that includes the US.")

with row2[2]:
    companies = sorted(df["company"].dropna().unique())
    picked_company = st.selectbox("Company", ["All", *companies])

with row2[3]:
    salary_only = st.toggle("Has salary")

show_overseas_note = scope in ("Overseas", "All targets", "Everything")

# --- Apply ---------------------------------------------------------------------------
view = df
if picked_countries:
    view = view[view["country"].isin(picked_countries)]
if status == "Active":
    view = view[view["is_active"] == 1]
elif status == "Closed":
    view = view[view["is_active"] == 0]
if picked_categories:
    view = view[view["category"].isin(picked_categories)]
if picked_levels:
    view = view[view["seniority"].isin(picked_levels)]
if keyword:
    needle = keyword.strip().lower()
    view = view[
        view["title"].str.lower().str.contains(needle, na=False)
        | view["company"].str.lower().str.contains(needle, na=False)
    ]
if picked_workplace:
    view = view[view["workplace_type"].isin(picked_workplace)]
if picked_states:
    # Scoped to US rows: a state filter must not silently delete every SG posting.
    view = view[(view["country"] != "US") | (view["subregion"].isin(picked_states))]
if salary_only:
    view = view[view["salary_min"].notna()]
if picked_company != "All":
    view = view[view["company"] == picked_company]

# --- Summary -------------------------------------------------------------------------
stats = st.columns(4)
stats[0].metric("Postings", f"{len(view):,}")
stats[1].metric("Companies", f"{view['company'].nunique():,}")
stats[2].metric(
    "Hybrid / onsite", f"{int(view['workplace_type'].isin(['hybrid', 'onsite']).sum()):,}"
)
stats[3].metric("With salary", f"{int(view['salary_min'].notna().sum()):,}")

if show_overseas_note:
    st.caption(
        "Overseas roles carry a relocation question Singapore roles don't, so **Workplace** "
        "and **US states** default to the shape you asked for: California, New York and "
        "Massachusetts, onsite or hybrid, remote excluded. Visa status is a column rather "
        "than a filter — most postings never mention it, so filtering on it would discard "
        "workable roles alongside the genuinely closed ones."
    )

if not classified:
    st.warning(
        "**Most of these postings are not roles you want.** Across the four target "
        "regions, 31% are sales, legal, HR, finance or ops, and much of the rest is "
        "generic SWE, PM and design. The filters that would cut them — role category, "
        "seniority, visa sponsorship — are all produced by Phase 2 classification, and "
        "are disabled above until it runs. Keyword matching cannot substitute: it misses "
        "*Member of Technical Staff* (often an MLE) and catches *Data Center Project "
        "Manager* (construction).",
        icon=":material/filter_alt_off:",
    )

st.divider()

# --- Table ---------------------------------------------------------------------------
if view.empty:
    st.warning("No postings match these filters.")
else:
    table = view.sort_values("first_seen", ascending=False).copy()

    def format_salary(currency, low, high) -> str:
        """Render a range only from the parts that are actually present.

        Both None and NaN occur here: NaN from pandas' float columns, None straight from
        SQLite. `low == low` catches only the former, so test with pd.notna.
        """
        if not (isinstance(currency, str) and currency):
            return ""
        has_low, has_high = pd.notna(low), pd.notna(high)
        if has_low and has_high:
            return f"{currency} {low:,.0f}–{high:,.0f}"
        if has_low:
            return f"{currency} {low:,.0f}+"
        if has_high:
            return f"{currency} up to {high:,.0f}"
        return ""

    table["salary"] = [
        format_salary(c, lo, hi)
        for c, lo, hi in zip(
            table["salary_currency"], table["salary_min"], table["salary_max"], strict=True
        )
    ]
    table["category_label"] = [
        label_for(c) if isinstance(c, str) else "—" for c in table["category"]
    ]

    table["workplace"] = table["workplace_type"].replace("unknown", "—")
    # Shown, never filtered on: most JDs simply don't mention sponsorship, so filtering
    # would discard workable roles alongside the genuinely closed ones.
    table["visa"] = table["visa_sponsorship"].fillna("—")

    columns = {
        "title": st.column_config.TextColumn("Title", width="large"),
        "company": st.column_config.TextColumn("Company"),
        "category_label": st.column_config.TextColumn("Category"),
        "seniority": st.column_config.TextColumn("Level"),
        "location_raw": st.column_config.TextColumn("Location"),
        "country": st.column_config.TextColumn("Region", width="small"),
        "subregion": st.column_config.TextColumn("State", width="small"),
        "workplace": st.column_config.TextColumn("Workplace", width="small"),
        "visa": st.column_config.TextColumn("Visa", width="small"),
        "salary": st.column_config.TextColumn("Salary"),
        "first_seen": st.column_config.DatetimeColumn("First seen", format="YYYY-MM-DD"),
        "url": st.column_config.LinkColumn("Link", display_text="open"),
    }
    st.dataframe(
        table[list(columns)],
        column_config=columns,
        hide_index=True,
        width="stretch",
        height=620,
    )

    st.download_button(
        "Download as CSV",
        table[list(columns)].to_csv(index=False).encode("utf-8"),
        file_name="tideline-jobs.csv",
        mime="text/csv",
    )

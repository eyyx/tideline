"""Page 2 — Market trends (the market layer, PLAN §9)."""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import (  # noqa: E402
    BASELINE_COLOR,
    TIER1_ORDER,
    base_chart_props,
    classification_notice,
    color_scale,
    is_classified,
    label_for,
    load_jobs,
    page_header,
)

st.set_page_config(page_title="tideline — market", page_icon="🌊", layout="wide")

page_header(
    "Market trends",
    "Posting volume over time by role category, with the generic-SWE baseline for "
    "overall hiring temperature.",
)

df = load_jobs()

if not is_classified(df):
    classification_notice()
    st.stop()

# --- Filters -------------------------------------------------------------------------
controls = st.columns([1.2, 1.2, 2])
with controls[0]:
    regions = st.multiselect("Region", ["SG", "US", "AU", "NL"], default=["SG", "US", "AU", "NL"])
with controls[1]:
    show_baseline = st.toggle("Show SWE baseline", value=True)

view = df[df["country"].isin(regions)] if regions else df
tier1 = view[view["category"].isin(TIER1_ORDER)]

# Weekly cohorts by first sighting: when this system first saw the posting, which is the
# only date available for every source (`posted_at` is absent or approximate on some).
weekly = (
    tier1.assign(week=tier1["first_seen"].dt.to_period("W").dt.start_time)
    .groupby(["week", "category"], as_index=False)
    .size()
    .rename(columns={"size": "postings"})
)

span_days = (view["first_seen"].max() - view["first_seen"].min()).days if len(view) else 0
if span_days < 14:
    st.warning(
        f"**Only {span_days + 1} day(s) of history so far.** A trend needs weeks of "
        "accumulation before it means anything — the chart below is correct but not yet "
        "informative. This is why the plan puts ingest before the dashboard.",
        icon=":material/schedule:",
    )

st.subheader("Weekly postings by category")

if weekly.empty:
    st.warning("No tier-1 postings in this selection.")
else:
    slugs = sorted(weekly["category"].unique())
    weekly["label"] = weekly["category"].map(label_for)

    hover = alt.selection_point(fields=["week"], nearest=True, on="pointerover", empty=False)

    lines = (
        alt.Chart(weekly)
        .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=36, filled=True))
        .encode(
            x=alt.X("week:T", title=None),
            y=alt.Y("postings:Q", title="New postings"),
            color=alt.Color(
                "category:N",
                scale=color_scale(slugs),
                legend=alt.Legend(title=None, labelExpr="", orient="bottom"),
                sort=list(TIER1_ORDER),
            ),
            detail="category:N",
            tooltip=[
                alt.Tooltip("week:T", title="Week of", format="%Y-%m-%d"),
                alt.Tooltip("label:N", title="Category"),
                alt.Tooltip("postings:Q", title="Postings"),
            ],
        )
    )

    layers = [lines]

    if show_baseline:
        baseline = (
            view[view["category"] == "software_developer"]
            .assign(week=lambda d: d["first_seen"].dt.to_period("W").dt.start_time)
            .groupby("week", as_index=False)
            .size()
            .rename(columns={"size": "postings"})
        )
        if not baseline.empty:
            layers.insert(
                0,
                alt.Chart(baseline)
                .mark_line(strokeWidth=2, color=BASELINE_COLOR, strokeDash=[4, 3])
                .encode(
                    x="week:T",
                    y="postings:Q",
                    tooltip=[
                        alt.Tooltip("week:T", title="Week of", format="%Y-%m-%d"),
                        alt.Tooltip("postings:Q", title="Software Developer (baseline)"),
                    ],
                ),
            )

    rule = (
        alt.Chart(weekly)
        .mark_rule(color="#c3c2b7")
        .encode(x="week:T", opacity=alt.condition(hover, alt.value(0.4), alt.value(0)))
        .add_params(hover)
    )

    chart = alt.layer(*layers, rule).properties(height=380).interactive(bind_y=False)
    st.altair_chart(base_chart_props(chart), width="stretch")

    if show_baseline:
        st.caption(
            "The dashed grey line is `software_developer` — a tier-2 baseline for overall "
            "tech-hiring temperature, not a target role. Read the coloured lines relative "
            "to it: all rising together is a hot market, not a shift toward AI roles."
        )

    # The light-mode palette puts three slots under 3:1 contrast, so a table view is
    # required relief, not a nicety.
    with st.expander("Table view"):
        st.dataframe(
            weekly.pivot(index="week", columns="label", values="postings").fillna(0).astype(int),
            width="stretch",
        )

st.divider()

# --- Regional comparison -------------------------------------------------------------
st.subheader("Category mix by region")

by_region = (
    tier1.groupby(["country", "category"], as_index=False).size().rename(columns={"size": "n"})
)
if not by_region.empty:
    by_region["label"] = by_region["category"].map(label_for)
    bars = (
        alt.Chart(by_region)
        .mark_bar(cornerRadiusEnd=4, stroke="#fcfcfb", strokeWidth=2)
        .encode(
            x=alt.X("n:Q", title="Postings", stack="zero"),
            y=alt.Y("country:N", title=None, sort=["SG", "AU", "NL", "US"]),
            color=alt.Color(
                "category:N",
                scale=color_scale(sorted(by_region["category"].unique())),
                legend=alt.Legend(title=None, orient="bottom"),
                sort=list(TIER1_ORDER),
            ),
            tooltip=[
                alt.Tooltip("country:N", title="Region"),
                alt.Tooltip("label:N", title="Category"),
                alt.Tooltip("n:Q", title="Postings"),
            ],
        )
        .properties(height=200)
    )
    st.altair_chart(base_chart_props(bars), width="stretch")
    st.caption(
        "Absolute counts, so the US bar dominates by market size. Compare *shape*, not "
        "length — the question is which categories are proportionally larger where."
    )

st.divider()

# --- DA vs DS/MLE, the transition question -------------------------------------------
st.subheader("Data Analyst vs DS / ML Engineer")
st.caption(
    "Supply comparison for the career-transition question in PLAN §1: how many roles "
    "exist on each side, per region."
)

transition = tier1[tier1["category"].isin(["data_analyst", "data_scientist", "ml_engineer"])]
if transition.empty:
    st.info("No postings in these three categories yet.")
else:
    counts = (
        transition.groupby(["country", "category"], as_index=False)
        .size()
        .rename(columns={"size": "n"})
    )
    counts["label"] = counts["category"].map(label_for)
    grouped = (
        alt.Chart(counts)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("country:N", title=None, sort=["SG", "AU", "NL", "US"]),
            y=alt.Y("n:Q", title="Postings"),
            xOffset=alt.XOffset("category:N", sort=list(TIER1_ORDER)),
            color=alt.Color(
                "category:N",
                scale=color_scale(sorted(counts["category"].unique())),
                legend=alt.Legend(title=None, orient="bottom"),
                sort=list(TIER1_ORDER),
            ),
            tooltip=[
                alt.Tooltip("country:N", title="Region"),
                alt.Tooltip("label:N", title="Category"),
                alt.Tooltip("n:Q", title="Postings"),
            ],
        )
        .properties(height=280)
    )
    st.altair_chart(base_chart_props(grouped), width="stretch")

    pivot = counts.pivot(index="country", columns="label", values="n").fillna(0).astype(int)
    st.dataframe(pivot, width="stretch")

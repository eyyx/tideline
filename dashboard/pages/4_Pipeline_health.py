"""Page 4 — Pipeline health (internal, PLAN §9).

Answers one question: is the data I'm looking at on the other pages trustworthy?
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import base_chart_props, load_jobs, load_runs, page_header  # noqa: E402

st.set_page_config(page_title="tideline — health", page_icon="🌊", layout="wide")

page_header("Pipeline health", "Whether the numbers on the other pages can be trusted.")

runs = load_runs()
jobs = load_jobs()

if runs.empty:
    st.warning("No ingest runs recorded yet.")
    st.stop()

latest_run = runs["run_at"].max()
latest = runs[runs["run_at"] == latest_run]
failed = latest[latest["ok"] == 0]
pending_classification = int(jobs["category"].isna().sum())

cols = st.columns(4)
cols[0].metric("Last run", latest_run.strftime("%Y-%m-%d %H:%M UTC"))
cols[1].metric("Boards OK", f"{int((latest['ok'] == 1).sum())}/{len(latest)}")
cols[2].metric("Active postings", f"{int((jobs['is_active'] == 1).sum()):,}")
cols[3].metric("Awaiting classification", f"{pending_classification:,}")

age_hours = (pd.Timestamp.now(tz="UTC") - latest_run).total_seconds() / 3600
if age_hours > 30:
    st.error(
        f"Last successful run was {age_hours:.0f} hours ago. The cron runs twice daily, "
        "so anything past ~30 hours means the workflow is failing or disabled.",
        icon=":material/error:",
    )

if not failed.empty:
    st.subheader(f"Failed boards in the last run ({len(failed)})")
    st.dataframe(
        failed[["source", "error"]].rename(columns={"source": "Board", "error": "Error"}),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "A failed board is isolated: its postings keep their previous state rather than "
        "being marked closed, so one broken board never distorts the trend charts."
    )
else:
    st.success("Every board succeeded in the most recent run.", icon=":material/check_circle:")

st.divider()

# --- Volume over time ----------------------------------------------------------------
st.subheader("Postings seen per run")

per_run = (
    runs[runs["ok"] == 1]
    .groupby("run_at", as_index=False)
    .agg(jobs_seen=("jobs_seen", "sum"), jobs_new=("jobs_new", "sum"), boards=("source", "count"))
)

if len(per_run) < 2:
    st.info("Only one run so far — this chart needs a few runs before it shows anything.")
else:
    melted = per_run.melt(
        id_vars="run_at", value_vars=["jobs_seen", "jobs_new"], var_name="measure", value_name="n"
    )
    melted["measure"] = melted["measure"].map({"jobs_seen": "Seen", "jobs_new": "New"})
    chart = (
        alt.Chart(melted)
        .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=36, filled=True))
        .encode(
            x=alt.X("run_at:T", title=None),
            y=alt.Y("n:Q", title="Postings"),
            color=alt.Color(
                "measure:N",
                scale=alt.Scale(domain=["Seen", "New"], range=["#2a78d6", "#eb6834"]),
                legend=alt.Legend(title=None, orient="bottom"),
            ),
            tooltip=[
                alt.Tooltip("run_at:T", title="Run", format="%Y-%m-%d %H:%M"),
                alt.Tooltip("measure:N", title="Measure"),
                alt.Tooltip("n:Q", title="Postings"),
            ],
        )
        .properties(height=300)
    )
    st.altair_chart(base_chart_props(chart), width="stretch")
    st.caption(
        "'Seen' should stay roughly flat run to run; 'New' should be small after the first "
        "run. A spike in 'New' usually means a board changed its posting IDs, which creates "
        "duplicates that look like hiring growth."
    )

st.divider()

# --- Per-board detail ----------------------------------------------------------------
st.subheader("Board status")

board_health = (
    runs.sort_values("run_at")
    .groupby("source")
    .agg(
        last_run=("run_at", "max"),
        last_ok=("ok", "last"),
        runs=("ok", "count"),
        failures=("ok", lambda s: int((s == 0).sum())),
    )
    .reset_index()
    .sort_values(["last_ok", "failures"], ascending=[True, False])
)
board_health["status"] = board_health["last_ok"].map({1: "✅ ok", 0: "❌ failing"})

st.dataframe(
    board_health[["source", "status", "last_run", "runs", "failures"]].rename(
        columns={
            "source": "Board",
            "status": "Status",
            "last_run": "Last run",
            "runs": "Runs",
            "failures": "Failures",
        }
    ),
    hide_index=True,
    width="stretch",
    height=420,
)

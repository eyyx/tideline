"""Page 3 — Skills and emerging signals (PLAN §9)."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import (  # noqa: E402
    MUTED_INK,
    base_chart_props,
    classification_notice,
    is_classified,
    load_jobs,
    page_header,
)

st.set_page_config(page_title="tideline — skills", page_icon="🌊", layout="wide")

page_header(
    "Skills & emerging signals",
    "What employers are asking for, and which job titles are new.",
)

df = load_jobs()

#: Agent-era vocabulary worth watching specifically (PLAN §9).
AGENT_TERMS = {
    "langgraph",
    "langchain",
    "llamaindex",
    "mcp",
    "agentic",
    "autogen",
    "crewai",
    "dspy",
    "rag",
    "vector database",
    "pinecone",
    "weaviate",
    "pydantic-ai",
}

tab_skills, tab_titles = st.tabs(["Skill demand", "Emerging titles"])

# --- Emerging titles work WITHOUT classification, so this tab is always live ---------
with tab_titles:
    st.subheader("Newly appearing title n-grams")
    st.caption(
        "Bigrams and trigrams from job titles, ranked by frequency. Detecting genuinely "
        "*new* titles needs history to compare against — with a single ingest the honest "
        "version of this is a frequency baseline, which is what you see below. Once weeks "
        "of data exist this becomes first-appearance detection."
    )

    active = df[df["is_active"] == 1]
    stop = {
        "and",
        "of",
        "the",
        "for",
        "to",
        "in",
        "at",
        "a",
        "an",
        "with",
        "or",
        "senior",
        "staff",
        "principal",
        "lead",
        "junior",
        "i",
        "ii",
        "iii",
    }

    def ngrams(title: str, n: int) -> list[str]:
        words = [w for w in re.findall(r"[a-z0-9+#.]+", title.lower()) if w not in stop]
        return [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]

    n_size = st.radio("N-gram size", [2, 3], horizontal=True, key="ngram")
    counts = Counter()
    for title in active["title"]:
        counts.update(ngrams(title, n_size))

    top = pd.DataFrame(counts.most_common(25), columns=["ngram", "count"])
    if top.empty:
        st.info("No titles available.")
    else:
        top["is_agent"] = top["ngram"].str.contains(
            "|".join(sorted(AGENT_TERMS)), case=False, regex=True
        )
        bars = (
            alt.Chart(top)
            .mark_bar(cornerRadiusEnd=4, color="#2a78d6")
            .encode(
                x=alt.X("count:Q", title="Postings"),
                y=alt.Y("ngram:N", title=None, sort="-x"),
                tooltip=[
                    alt.Tooltip("ngram:N", title="Phrase"),
                    alt.Tooltip("count:Q", title="Postings"),
                ],
            )
            .properties(height=560)
        )
        labels = bars.mark_text(align="left", dx=6, color=MUTED_INK, fontSize=11).encode(
            text="count:Q"
        )
        st.altair_chart(base_chart_props(alt.layer(bars, labels)), width="stretch")

# --- Skill demand needs the classifications table ------------------------------------
with tab_skills:
    if not is_classified(df):
        classification_notice()
        st.stop()

    classified = df[df["skills"].notna()].copy()
    rows: list[dict] = []
    for _, row in classified.iterrows():
        try:
            skills = json.loads(row["skills"])
        except (TypeError, ValueError):
            continue
        for skill in skills:
            rows.append(
                {
                    "skill": skill,
                    "country": row["country"],
                    "category": row["category"],
                    "week": row["first_seen"],
                }
            )

    if not rows:
        st.info("No skills extracted yet.")
        st.stop()

    skills_df = pd.DataFrame(rows)

    controls = st.columns([1.2, 1.2, 2])
    with controls[0]:
        regions = st.multiselect(
            "Region", ["SG", "US", "AU", "NL"], default=["SG", "US", "AU", "NL"]
        )
    with controls[1]:
        agent_only = st.toggle(
            "Agent tooling only", help=f"Filters to: {', '.join(sorted(AGENT_TERMS))}"
        )

    scoped = skills_df[skills_df["country"].isin(regions)] if regions else skills_df
    if agent_only:
        scoped = scoped[scoped["skill"].isin(AGENT_TERMS)]

    st.subheader("Most-requested skills")
    top_skills = (
        scoped.groupby("skill", as_index=False)
        .size()
        .rename(columns={"size": "n"})
        .sort_values("n", ascending=False)
        .head(30)
    )

    if top_skills.empty:
        st.warning("No skills match this selection.")
    else:
        bars = (
            alt.Chart(top_skills)
            .mark_bar(cornerRadiusEnd=4, color="#2a78d6")
            .encode(
                x=alt.X("n:Q", title="Postings mentioning"),
                y=alt.Y("skill:N", title=None, sort="-x"),
                tooltip=[
                    alt.Tooltip("skill:N", title="Skill"),
                    alt.Tooltip("n:Q", title="Postings"),
                ],
            )
            .properties(height=min(640, 24 * len(top_skills) + 40))
        )
        labels = bars.mark_text(align="left", dx=6, color=MUTED_INK, fontSize=11).encode(text="n:Q")
        st.altair_chart(base_chart_props(alt.layer(bars, labels)), width="stretch")

        with st.expander("Table view"):
            st.dataframe(top_skills, hide_index=True, width="stretch")

    st.divider()
    st.subheader("Time to fill")
    st.caption("`closed_at − first_seen` for postings that have disappeared from their board.")

    closed = df[df["closed_at"].notna()].copy()
    if closed.empty:
        st.info(
            "No postings have closed yet. A posting is marked closed after two consecutive "
            "successful runs without seeing it, so this needs a few days of pipeline history."
        )
    else:
        closed["days"] = (closed["closed_at"] - closed["first_seen"]).dt.total_seconds() / 86400
        hist = (
            alt.Chart(closed)
            .mark_bar(cornerRadiusEnd=4, color="#2a78d6", stroke="#fcfcfb", strokeWidth=2)
            .encode(
                x=alt.X("days:Q", bin=alt.Bin(maxbins=30), title="Days open"),
                y=alt.Y("count():Q", title="Postings"),
                tooltip=[alt.Tooltip("count():Q", title="Postings")],
            )
            .properties(height=280)
        )
        st.altair_chart(base_chart_props(hist), width="stretch")

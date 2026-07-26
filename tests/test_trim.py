"""Description trimming — the mechanism that keeps the committed DB from growing
unboundedly in git history."""

import pytest

from tideline.db import connect, init_schema, trim_classified_descriptions, upsert_job
from tideline.models import DESCRIPTION_MAX_CHARS, DESCRIPTION_PREVIEW_CHARS, NormalizedJob

NOW = "2026-07-26T00:00:00+00:00"
LONG_TEXT = "x" * 3000


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def add_job(conn, job_id_hint: str, *, classified: bool, description=LONG_TEXT) -> int:
    upsert_job(
        conn,
        NormalizedJob(
            source="greenhouse",
            source_job_id=job_id_hint,
            company="Anthropic",
            title="Data Scientist",
            description=description,
        ),
        NOW,
    )
    job_id = conn.execute("SELECT id FROM jobs WHERE source_job_id = ?", (job_id_hint,)).fetchone()[
        "id"
    ]
    if classified:
        conn.execute(
            "INSERT INTO classifications (job_id, category, tier, model, classified_at)"
            " VALUES (?, 'data_scientist', 1, 'claude-haiku-4-5', ?)",
            (job_id, NOW),
        )
    return job_id


def desc_len(conn, job_id: int) -> int:
    return conn.execute(
        "SELECT LENGTH(description) AS n FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()["n"]


def test_trims_only_classified_jobs(conn):
    classified = add_job(conn, "1", classified=True)
    pending = add_job(conn, "2", classified=False)

    assert trim_classified_descriptions(conn) == 1
    assert desc_len(conn, classified) == DESCRIPTION_PREVIEW_CHARS
    assert desc_len(conn, pending) == len(LONG_TEXT)  # still needed by the classifier


def test_is_idempotent(conn):
    add_job(conn, "1", classified=True)

    assert trim_classified_descriptions(conn) == 1
    assert trim_classified_descriptions(conn) == 0  # nothing left to do


def test_short_descriptions_are_untouched(conn):
    job_id = add_job(conn, "1", classified=True, description="short")

    assert trim_classified_descriptions(conn) == 0
    assert desc_len(conn, job_id) == len("short")


def test_eval_sample_keeps_full_text(conn):
    """Labelled rows must survive intact so prompt revisions stay re-scorable."""
    protected = add_job(conn, "1", classified=True)
    other = add_job(conn, "2", classified=True)

    assert trim_classified_descriptions(conn, keep_job_ids=[protected]) == 1
    assert desc_len(conn, protected) == len(LONG_TEXT)
    assert desc_len(conn, other) == DESCRIPTION_PREVIEW_CHARS


def test_null_descriptions_do_not_break_trimming(conn):
    add_job(conn, "1", classified=True, description=None)
    assert trim_classified_descriptions(conn) == 0


def test_ingest_cap_matches_what_the_classifier_reads(conn):
    """Storing more than the classifier's input window is bytes nothing reads."""
    job = NormalizedJob(
        source="greenhouse",
        source_job_id="9",
        company="Anthropic",
        title="Data Scientist",
        description="y" * 20_000,
    )
    assert len(job.description) == DESCRIPTION_MAX_CHARS == 4000

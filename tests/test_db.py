"""DB write paths. The pipeline reruns on a cron, so idempotency is the core property."""

import pytest

from tideline.db import connect, count_jobs, init_schema, record_run, upsert_job
from tideline.lifecycle import close_stale_jobs
from tideline.models import NormalizedJob

T1 = "2026-07-20T00:00:00+00:00"
T2 = "2026-07-21T00:00:00+00:00"
T3 = "2026-07-22T00:00:00+00:00"


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def make_job(**overrides) -> NormalizedJob:
    base = dict(
        source="greenhouse",
        source_job_id="1",
        company="Anthropic",
        title="Data Scientist",
        location_raw="Singapore",
        country="SG",
    )
    return NormalizedJob(**{**base, **overrides})


def test_init_schema_is_idempotent(conn):
    init_schema(conn)  # second run must not raise
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "classifications", "alerts_sent", "ingest_runs"} <= tables


def test_first_insert_reports_new(conn):
    assert upsert_job(conn, make_job(), T1) == "new"
    assert count_jobs(conn) == 1


def test_reingest_updates_without_duplicating(conn):
    upsert_job(conn, make_job(), T1)
    assert upsert_job(conn, make_job(), T2) == "updated"

    assert count_jobs(conn) == 1
    row = conn.execute("SELECT first_seen, last_seen FROM jobs").fetchone()
    assert row["first_seen"] == T1  # preserved
    assert row["last_seen"] == T2  # advanced


def test_mutable_fields_are_refreshed(conn):
    upsert_job(conn, make_job(), T1)
    upsert_job(conn, make_job(title="Senior Data Scientist"), T2)

    assert conn.execute("SELECT title FROM jobs").fetchone()["title"] == "Senior Data Scientist"


def test_same_id_across_sources_stays_distinct(conn):
    upsert_job(conn, make_job(source="greenhouse", source_job_id="1"), T1)
    upsert_job(conn, make_job(source="lever", source_job_id="1"), T1)

    assert count_jobs(conn) == 2


def test_adzuna_collapses_onto_authoritative_ats_row(conn):
    upsert_job(conn, make_job(company="Anthropic", title="Data Scientist"), T1)
    result = upsert_job(
        conn,
        make_job(
            source="adzuna", source_job_id="az-9", company="Anthropic, Inc.", title="data scientist"
        ),
        T2,
    )

    assert result == "deduped"
    assert count_jobs(conn) == 1
    assert conn.execute("SELECT last_seen FROM jobs").fetchone()["last_seen"] == T2


def test_adzuna_without_ats_twin_is_inserted(conn):
    assert upsert_job(conn, make_job(source="adzuna", source_job_id="az-1"), T1) == "new"
    assert count_jobs(conn) == 1


def test_reappearing_job_is_reopened(conn):
    upsert_job(conn, make_job(), T1)
    conn.execute("UPDATE jobs SET is_active = 0, closed_at = ?", (T2,))

    upsert_job(conn, make_job(), T3)
    row = conn.execute("SELECT is_active, closed_at FROM jobs").fetchone()
    assert row["is_active"] == 1
    assert row["closed_at"] is None


def test_source_salary_is_not_overwritten_by_a_later_blank(conn):
    upsert_job(conn, make_job(salary_min=100.0, salary_max=200.0, salary_currency="USD"), T1)
    upsert_job(conn, make_job(), T2)  # a run where compensation was not published

    row = conn.execute("SELECT salary_min, salary_currency FROM jobs").fetchone()
    assert row["salary_min"] == 100.0
    assert row["salary_currency"] == "USD"


class TestClosing:
    BOARD = "greenhouse:anthropic"

    def close(self, conn, now, company="Anthropic", board=None):
        return close_stale_jobs(
            conn,
            board=board or self.BOARD,
            source="greenhouse",
            company=company,
            now=now,
        )

    def test_needs_two_successful_runs_of_history(self, conn):
        upsert_job(conn, make_job(), T1)
        record_run(conn, run_at=T2, source=self.BOARD, ok=True)

        assert self.close(conn, T2) == 0
        assert count_jobs(conn, active_only=True) == 1

    def test_closes_after_two_consecutive_misses(self, conn):
        upsert_job(conn, make_job(), T1)
        for t in (T1, T2, T3):
            record_run(conn, run_at=t, source=self.BOARD, ok=True)

        assert self.close(conn, T3) == 1
        row = conn.execute("SELECT is_active, closed_at FROM jobs").fetchone()
        assert row["is_active"] == 0
        assert row["closed_at"] == T3

    def test_failed_runs_do_not_count_as_sighting_opportunities(self, conn):
        """A board outage must never look like a wave of closures."""
        upsert_job(conn, make_job(), T1)
        record_run(conn, run_at=T1, source=self.BOARD, ok=True)
        record_run(conn, run_at=T2, source=self.BOARD, ok=False, error="timeout")
        record_run(conn, run_at=T3, source=self.BOARD, ok=False, error="timeout")

        # Only one successful run in history, so nothing may be closed.
        assert self.close(conn, T3) == 0
        assert count_jobs(conn, active_only=True) == 1

    def test_one_failing_board_does_not_close_a_healthy_neighbour(self, conn):
        """The whole reason closing is per-board: Anthropic's board failing must not
        close Stripe's postings, and vice versa."""
        upsert_job(conn, make_job(company="Anthropic", source_job_id="1"), T1)
        upsert_job(conn, make_job(company="Stripe", source_job_id="2"), T1)
        for t in (T1, T2, T3):
            record_run(conn, run_at=t, source="greenhouse:stripe", ok=True)
            record_run(conn, run_at=t, source=self.BOARD, ok=False, error="timeout")

        # Stripe's board is healthy but its jobs were seen; Anthropic's never fetched.
        assert self.close(conn, T3, company="Anthropic") == 0
        assert self.close(conn, T3, company="Stripe", board="greenhouse:stripe") == 1

        rows = {
            r["company"]: r["is_active"]
            for r in conn.execute("SELECT company, is_active FROM jobs")
        }
        assert rows == {"Anthropic": 1, "Stripe": 0}

    def test_closing_is_scoped_to_one_source(self, conn):
        upsert_job(conn, make_job(source="greenhouse", source_job_id="1"), T1)
        upsert_job(conn, make_job(source="lever", source_job_id="2"), T1)
        for t in (T1, T2, T3):
            record_run(conn, run_at=t, source=self.BOARD, ok=True)

        self.close(conn, T3)
        active = conn.execute("SELECT source FROM jobs WHERE is_active = 1").fetchall()
        assert [r["source"] for r in active] == ["lever"]

"""Schema migration on an existing database.

The pipeline reads the DB it committed on the previous run, so a schema change that
isn't migrated fails in CI while passing locally against a freshly created file. These
tests exercise the old-database path specifically.
"""

import sqlite3

import pytest

from tideline.db import _MIGRATIONS, connect, init_schema, upsert_job
from tideline.models import NormalizedJob

NOW = "2026-07-26T00:00:00+00:00"

#: The jobs table as it shipped before subregion/workplace_type existed.
LEGACY_SCHEMA = """
CREATE TABLE jobs (
  id             INTEGER PRIMARY KEY,
  source         TEXT NOT NULL,
  source_job_id  TEXT NOT NULL,
  company        TEXT NOT NULL,
  title          TEXT NOT NULL,
  location_raw   TEXT,
  country        TEXT,
  is_remote      INTEGER DEFAULT 0,
  url            TEXT,
  description    TEXT,
  posted_at      TEXT,
  first_seen     TEXT NOT NULL,
  last_seen      TEXT NOT NULL,
  is_active      INTEGER NOT NULL DEFAULT 1,
  closed_at      TEXT,
  dedupe_key     TEXT,
  salary_min       REAL,
  salary_max       REAL,
  salary_currency  TEXT,
  UNIQUE(source, source_job_id)
);
"""


@pytest.fixture
def legacy_conn():
    conn = connect(":memory:")
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO jobs (source, source_job_id, company, title, first_seen, last_seen)"
        " VALUES ('greenhouse', 'legacy-1', 'Anthropic', 'Data Scientist', ?, ?)",
        (NOW, NOW),
    )
    conn.commit()
    yield conn
    conn.close()


def test_adds_missing_columns_to_an_existing_table(legacy_conn):
    init_schema(legacy_conn)

    columns = {row[1] for row in legacy_conn.execute("PRAGMA table_info(jobs)")}
    for _, column, _ in _MIGRATIONS:
        assert column in columns


def test_preserves_existing_rows(legacy_conn):
    init_schema(legacy_conn)

    row = legacy_conn.execute("SELECT company, title FROM jobs").fetchone()
    assert row["company"] == "Anthropic"
    assert row["title"] == "Data Scientist"


def test_upsert_works_after_migration(legacy_conn):
    """The actual CI failure mode: new code writing new columns to an old database."""
    init_schema(legacy_conn)

    result = upsert_job(
        legacy_conn,
        NormalizedJob(
            source="greenhouse",
            source_job_id="legacy-1",
            company="Anthropic",
            title="Data Scientist",
            country="US",
            subregion="CA",
            workplace_type="hybrid",
        ),
        NOW,
    )

    assert result == "updated"
    row = legacy_conn.execute("SELECT subregion, workplace_type FROM jobs").fetchone()
    assert row["subregion"] == "CA"
    assert row["workplace_type"] == "hybrid"


def test_is_idempotent(legacy_conn):
    init_schema(legacy_conn)
    init_schema(legacy_conn)  # must not raise "duplicate column name"


def test_fresh_database_needs_no_migration():
    conn = connect(":memory:")
    init_schema(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for _, column, _ in _MIGRATIONS:
        assert column in columns
    conn.close()


def test_migration_runs_before_schema_on_a_partial_database():
    """A database missing the table entirely must not trip the migration."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)  # no tables at all yet

    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    conn.close()

"""SQLite schema, connection handling, and write paths.

Every write here is idempotent: re-running the pipeline must not create duplicate rows
(PLAN §4). The DB file is committed to the repo, so schema changes must stay additive.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from tideline.lifecycle import dedupe_key_for
from tideline.models import DESCRIPTION_PREVIEW_CHARS, NormalizedJob

DEFAULT_DB_PATH = Path("data/jobs.db")

UpsertResult = Literal["new", "updated", "deduped"]
"""new = first sighting; updated = already known, timestamps refreshed;
deduped = an Adzuna posting that collapsed onto an authoritative ATS row."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id             INTEGER PRIMARY KEY,
  source         TEXT NOT NULL,
  source_job_id  TEXT NOT NULL,
  company        TEXT NOT NULL,
  title          TEXT NOT NULL,
  location_raw   TEXT,
  country        TEXT,
  subregion      TEXT,
  workplace_type TEXT DEFAULT 'unknown',
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

CREATE INDEX IF NOT EXISTS idx_jobs_dedupe    ON jobs(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_country   ON jobs(country, is_active);
CREATE INDEX IF NOT EXISTS idx_jobs_subregion ON jobs(country, subregion);
CREATE INDEX IF NOT EXISTS idx_jobs_source    ON jobs(source, is_active);

CREATE TABLE IF NOT EXISTS classifications (
  job_id           INTEGER PRIMARY KEY REFERENCES jobs(id),
  category         TEXT NOT NULL,
  tier             INTEGER NOT NULL,
  seniority        TEXT,
  skills           TEXT,
  salary_min       REAL,
  salary_max       REAL,
  salary_currency  TEXT,
  visa_sponsorship TEXT,
  confidence       REAL,
  model            TEXT NOT NULL,
  classified_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_class_category ON classifications(category, tier);

CREATE TABLE IF NOT EXISTS alerts_sent (
  job_id    INTEGER REFERENCES jobs(id),
  rule_name TEXT,
  sent_at   TEXT NOT NULL,
  PRIMARY KEY (job_id, rule_name)
);

CREATE TABLE IF NOT EXISTS ingest_runs (
  run_at     TEXT NOT NULL,
  source     TEXT NOT NULL,
  ok         INTEGER NOT NULL,
  jobs_seen  INTEGER,
  jobs_new   INTEGER,
  error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_source ON ingest_runs(source, run_at);

-- Batch API submissions span runs: we submit today and collect on a later run, so the
-- in-flight job set must survive process exit or those jobs get billed twice.
CREATE TABLE IF NOT EXISTS classify_batches (
  batch_id     TEXT PRIMARY KEY,
  submitted_at TEXT NOT NULL,
  job_ids      TEXT NOT NULL,      -- JSON array; the custom_ids we sent
  job_count    INTEGER NOT NULL,
  collected_at TEXT,               -- NULL while still in flight
  error        TEXT
);
"""


def utcnow_iso() -> str:
    """Current UTC time as an ISO 8601 string — the only timestamp format we store."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL survives the interrupted-run case better and costs nothing here.
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


#: Columns added after the first release. `CREATE TABLE IF NOT EXISTS` is a no-op on an
#: existing table, so a committed database never gains new columns on its own — and the
#: pipeline reads its own committed DB on every CI run. Without this, the run after any
#: schema change fails with "no such column" in CI while passing locally.
#: Additive only: SQLite cannot drop or retype a column, and the DB is version-controlled.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("jobs", "subregion", "TEXT"),
    ("jobs", "workplace_type", "TEXT DEFAULT 'unknown'"),
)


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """Bring an existing database up to the current schema. Returns what it added."""
    applied: list[str] = []
    for table, column, ddl in _MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # Table not created yet; SCHEMA will define it complete.
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            applied.append(f"{table}.{column}")
    return applied


def init_schema(conn: sqlite3.Connection) -> None:
    applied = _migrate(conn)
    conn.executescript(SCHEMA)
    conn.commit()
    if applied:
        import logging

        logging.getLogger("tideline").info("migrated: added %s", ", ".join(applied))


def upsert_job(conn: sqlite3.Connection, job: NormalizedJob, now: str) -> UpsertResult:
    """Insert or refresh one posting. Safe to call repeatedly with the same input."""
    key = dedupe_key_for(job.company, job.title, job.country)

    existing = conn.execute(
        "SELECT id FROM jobs WHERE source = ? AND source_job_id = ?",
        (job.source, job.source_job_id),
    ).fetchone()

    if existing is not None:
        # Refresh mutable fields — titles get edited and descriptions get expanded.
        conn.execute(
            """
            UPDATE jobs SET
              last_seen = ?, is_active = 1, closed_at = NULL,
              title = ?, location_raw = ?, country = ?, subregion = ?,
              workplace_type = ?, is_remote = ?,
              url = ?, description = ?, posted_at = ?, dedupe_key = ?,
              salary_min = COALESCE(?, salary_min),
              salary_max = COALESCE(?, salary_max),
              salary_currency = COALESCE(?, salary_currency)
            WHERE id = ?
            """,
            (
                now,
                job.title,
                job.location_raw,
                job.country,
                job.subregion,
                job.workplace_type,
                int(job.is_remote),
                job.url,
                job.description,
                job.posted_at,
                key,
                job.salary_min,
                job.salary_max,
                job.salary_currency,
                existing["id"],
            ),
        )
        return "updated"

    # ATS data is authoritative: an Adzuna posting matching a known ATS fingerprint only
    # refreshes that row rather than creating a near-duplicate (PLAN §5).
    if job.source == "adzuna":
        twin = conn.execute(
            "SELECT id FROM jobs WHERE dedupe_key = ? AND source != 'adzuna' LIMIT 1",
            (key,),
        ).fetchone()
        if twin is not None:
            conn.execute(
                "UPDATE jobs SET last_seen = ?, is_active = 1, closed_at = NULL WHERE id = ?",
                (now, twin["id"]),
            )
            return "deduped"

    conn.execute(
        """
        INSERT INTO jobs (
          source, source_job_id, company, title, location_raw, country, subregion,
          workplace_type, is_remote, url, description, posted_at, first_seen,
          last_seen, is_active, dedupe_key, salary_min, salary_max, salary_currency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """,
        (
            job.source,
            job.source_job_id,
            job.company,
            job.title,
            job.location_raw,
            job.country,
            job.subregion,
            job.workplace_type,
            int(job.is_remote),
            job.url,
            job.description,
            job.posted_at,
            now,
            now,
            key,
            job.salary_min,
            job.salary_max,
            job.salary_currency,
        ),
    )
    return "new"


def record_run(
    conn: sqlite3.Connection,
    *,
    run_at: str,
    source: str,
    ok: bool,
    jobs_seen: int | None = None,
    jobs_new: int | None = None,
    error: str | None = None,
) -> None:
    """Append to the run log. Powers the pipeline-health page and the closing logic."""
    conn.execute(
        "INSERT INTO ingest_runs (run_at, source, ok, jobs_seen, jobs_new, error)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (run_at, source, int(ok), jobs_seen, jobs_new, error),
    )


def successful_run_times(conn: sqlite3.Connection, source: str, limit: int = 2) -> list[str]:
    """Most recent successful run timestamps for a source, newest first."""
    rows = conn.execute(
        "SELECT run_at FROM ingest_runs WHERE source = ? AND ok = 1 ORDER BY run_at DESC LIMIT ?",
        (source, limit),
    ).fetchall()
    return [r["run_at"] for r in rows]


def unclassified_jobs(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    """Jobs needing classification: no `classifications` row and not already in flight.

    Excluding in-flight batches is what makes classification idempotent across runs —
    without it, a rerun before collection resubmits and pays for the same jobs twice.
    """
    sql = """
        SELECT j.id, j.title, j.company, j.location_raw, j.description
        FROM jobs j
        LEFT JOIN classifications c ON c.job_id = j.id
        WHERE c.job_id IS NULL
          AND j.id NOT IN (
              SELECT CAST(value AS INTEGER) FROM classify_batches, json_each(job_ids)
              WHERE collected_at IS NULL
          )
        ORDER BY j.first_seen DESC
    """
    if limit is not None:
        sql += " LIMIT ?"
        return conn.execute(sql, (limit,)).fetchall()
    return conn.execute(sql).fetchall()


def insert_classification(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    category: str,
    tier: int,
    seniority: str | None,
    skills: list[str],
    salary_min: float | None,
    salary_max: float | None,
    salary_currency: str | None,
    visa_sponsorship: str | None,
    confidence: float | None,
    model: str,
    classified_at: str,
) -> None:
    """Store one classification. `INSERT OR IGNORE` so a double-collect is harmless."""
    conn.execute(
        """
        INSERT OR IGNORE INTO classifications (
          job_id, category, tier, seniority, skills, salary_min, salary_max,
          salary_currency, visa_sponsorship, confidence, model, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            category,
            tier,
            seniority,
            json.dumps(skills),
            salary_min,
            salary_max,
            salary_currency,
            visa_sponsorship,
            confidence,
            model,
            classified_at,
        ),
    )


def record_batch(
    conn: sqlite3.Connection, *, batch_id: str, job_ids: list[int], submitted_at: str
) -> None:
    conn.execute(
        "INSERT INTO classify_batches (batch_id, submitted_at, job_ids, job_count)"
        " VALUES (?, ?, ?, ?)",
        (batch_id, submitted_at, json.dumps(job_ids), len(job_ids)),
    )


def open_batches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT batch_id, submitted_at, job_count FROM classify_batches"
        " WHERE collected_at IS NULL ORDER BY submitted_at"
    ).fetchall()


def close_batch(
    conn: sqlite3.Connection, *, batch_id: str, collected_at: str, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE classify_batches SET collected_at = ?, error = ? WHERE batch_id = ?",
        (collected_at, error, batch_id),
    )


def trim_classified_descriptions(
    conn: sqlite3.Connection,
    *,
    preview_chars: int = DESCRIPTION_PREVIEW_CHARS,
    keep_job_ids: Iterable[int] = (),
) -> int:
    """Shrink descriptions of already-classified jobs down to a preview.

    Descriptions are ~85% of this file's bytes and are only ever read once, by the
    classifier. Afterwards the useful content lives in `classifications`, and the
    dashboard shows a preview plus a link to `url`. Keeping full text would grow the
    committed DB by roughly a gigabyte of git history per month.

    `keep_job_ids` protects the labelled eval sample, which must retain full text so
    prompt revisions can be re-scored against it (PLAN §7). Idempotent: rows already at
    or below the preview length are left alone. Returns the number of rows trimmed.
    """
    keep = tuple(keep_job_ids)
    exclusion = ""
    params: list[object] = [preview_chars, preview_chars]
    if keep:
        exclusion = f" AND j.id NOT IN ({','.join('?' * len(keep))})"
        params.extend(keep)

    cur = conn.execute(
        f"""
        UPDATE jobs SET description = SUBSTR(description, 1, ?)
        WHERE id IN (
            SELECT j.id FROM jobs j
            JOIN classifications c ON c.job_id = j.id
            WHERE j.description IS NOT NULL AND LENGTH(j.description) > ?{exclusion}
        )
        """,
        params,
    )
    trimmed = cur.rowcount
    conn.commit()
    if trimmed:
        # SQLite keeps freed pages unless told otherwise; without this the file never
        # actually shrinks and the whole exercise is pointless.
        conn.execute("VACUUM")
    return trimmed


def count_jobs(conn: sqlite3.Connection, *, active_only: bool = False) -> int:
    sql = "SELECT COUNT(*) AS n FROM jobs"
    if active_only:
        sql += " WHERE is_active = 1"
    return int(conn.execute(sql).fetchone()["n"])

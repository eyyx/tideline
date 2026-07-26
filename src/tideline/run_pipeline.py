"""Pipeline entry point: `python -m tideline.run_pipeline`.

Phase 1 covers ingest + upsert only; classify and alert land in later phases (PLAN §4).

Failure policy: one company's board failing must never affect another's data. Every
fetch is isolated, logged to `ingest_runs`, and its postings are left untouched rather
than being treated as closed.
"""

from __future__ import annotations

import argparse
import collections
import logging
import sqlite3
import sys
from pathlib import Path

from tideline.db import DEFAULT_DB_PATH, connect, count_jobs, init_schema, record_run, upsert_job
from tideline.ingest import ATS_ADAPTERS, CompanySpec, IngestError, load_companies, make_client
from tideline.ingest.base import DEFAULT_COMPANIES_PATH
from tideline.lifecycle import board_key, close_stale_jobs

log = logging.getLogger("tideline")


def ingest_company(
    conn: sqlite3.Connection,
    company: CompanySpec,
    client,
    now: str,
) -> tuple[bool, collections.Counter]:
    """Fetch and store one company's board. Returns (succeeded, upsert-result counts)."""
    board = board_key(company.ats, company.token)
    counts: collections.Counter = collections.Counter()

    try:
        jobs = ATS_ADAPTERS[company.ats](company, client)
    except IngestError as exc:
        log.warning("%-28s FAILED  %s", company.name, exc)
        record_run(conn, run_at=now, source=board, ok=False, error=str(exc))
        conn.commit()
        return False, counts
    except Exception as exc:  # noqa: BLE001 - an adapter bug must not abort the run
        log.exception("%-28s CRASHED", company.name)
        record_run(conn, run_at=now, source=board, ok=False, error=f"{type(exc).__name__}: {exc}")
        conn.commit()
        return False, counts

    for job in jobs:
        counts[upsert_job(conn, job, now)] += 1

    record_run(
        conn,
        run_at=now,
        source=board,
        ok=True,
        jobs_seen=len(jobs),
        jobs_new=counts["new"],
    )
    # Only a board we just fetched successfully is eligible for closing.
    closed = close_stale_jobs(conn, board=board, source=company.ats, company=company.name, now=now)
    conn.commit()

    log.info(
        "%-28s %4d seen  %4d new  %4d closed",
        company.name,
        len(jobs),
        counts["new"],
        closed,
    )
    counts["closed"] = closed
    return True, counts


def _classify_step(conn: sqlite3.Connection) -> None:
    """Classify newly ingested jobs, then reclaim their description bytes.

    Isolated like an adapter: a classification failure logs and returns rather than
    losing the ingest work this run already committed.
    """
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.info("classification skipped: ANTHROPIC_API_KEY not set")
        return

    try:
        import anthropic

        from tideline.classify.classifier import run_classification
        from tideline.db import trim_classified_descriptions

        result = run_classification(conn, anthropic.Anthropic())
        log.info(
            "classify: collected %d, submitted %d, inline %d",
            result["collected"],
            result["submitted"],
            result["classified_sync"],
        )
        # Descriptions are ~85% of this file's bytes and are only read once, by the
        # classifier. Reclaim them as soon as they've served their purpose (PLAN §5).
        trimmed = trim_classified_descriptions(conn)
        if trimmed:
            log.info("trimmed descriptions on %d classified jobs", trimmed)
    except Exception as exc:  # noqa: BLE001 - never let classification abort a run
        log.exception("classification step failed: %s", exc)


def run(
    db_path: Path,
    companies_path: Path,
    limit: int | None = None,
    classify: bool = False,
) -> int:
    """Execute one ingest pass. Returns a process exit code."""
    from tideline.db import utcnow_iso

    companies = load_companies(companies_path)
    if limit:
        companies = companies[:limit]

    conn = connect(db_path)
    init_schema(conn)
    now = utcnow_iso()

    totals: collections.Counter = collections.Counter()
    failures: list[str] = []

    with make_client() as client:
        for company in companies:
            ok, counts = ingest_company(conn, company, client, now)
            totals.update(counts)
            if not ok:
                failures.append(company.name)

    if classify:
        _classify_step(conn)

    total_rows = count_jobs(conn)
    active_rows = count_jobs(conn, active_only=True)
    # Fold the WAL back into the main file before anything commits it — a DB file
    # committed without its WAL is missing this run's writes.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    log.info(
        "run complete: %d/%d boards ok | %d new, %d updated, %d deduped, %d closed"
        " | %d rows (%d active)",
        len(companies) - len(failures),
        len(companies),
        totals["new"],
        totals["updated"],
        totals["deduped"],
        totals["closed"],
        total_rows,
        active_rows,
    )
    if failures:
        log.warning("failed boards (%d): %s", len(failures), ", ".join(failures))

    # A run is a failure only if every board failed — that means the network or our
    # code is broken, not that one company changed its ATS.
    return 1 if companies and len(failures) == len(companies) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest job postings into the tideline DB.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--companies", type=Path, default=DEFAULT_COMPANIES_PATH)
    parser.add_argument("--limit", type=int, help="Only process the first N companies.")
    parser.add_argument(
        "--classify",
        action="store_true",
        help="Classify new jobs after ingest (needs ANTHROPIC_API_KEY).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # One line per HTTP request buries the per-company summary in CI logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return run(args.db, args.companies, args.limit, classify=args.classify)


if __name__ == "__main__":
    sys.exit(main())

"""Posting lifecycle: cross-source dedupe fingerprints and active/closed determination.

Deliberately stdlib-only and free of imports from `db`, so the dependency runs one way
(db -> lifecycle) and these rules stay unit-testable without a database.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3

#: Legal-entity suffixes stripped before fingerprinting, so "Stripe, Inc." and "Stripe"
#: produce the same key. Ordered longest-first to avoid partial strips.
_COMPANY_SUFFIXES = (
    "incorporated",
    "corporation",
    "holdings",
    "limited",
    "company",
    "gmbh",
    "s.a.",
    "b.v.",
    "pte",
    "pty",
    "ltd",
    "llc",
    "inc",
    "plc",
    "corp",
    "co",
)

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    lowered = text.casefold()
    stripped = _PUNCT.sub(" ", lowered)
    return _WS.sub(" ", stripped).strip()


def normalize_company(name: str) -> str:
    """Normalize plus repeated legal-suffix removal ("Foo Pte Ltd" -> "foo")."""
    result = normalize(name)
    changed = True
    while changed:
        changed = False
        for suffix in _COMPANY_SUFFIXES:
            token = normalize(suffix)
            if result.endswith(" " + token):
                result = result[: -(len(token) + 1)].strip()
                changed = True
    return result


def dedupe_key_for(company: str, title: str, country: str) -> str:
    """Exact fingerprint used for cross-source dedupe (PLAN §5).

    v1 is deliberately exact rather than fuzzy: a false merge silently loses a real
    posting, which is worse for trend data than a rare duplicate.
    """
    payload = f"{normalize_company(company)}|{normalize(title)}|{country}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def board_key(ats: str, token: str) -> str:
    """Identity of one company's job board, used as `ingest_runs.source`.

    Health is tracked per board rather than per platform: one company's board failing
    while its neighbours succeed must not make that company's postings look closed.
    """
    return f"{ats}:{token}"


def close_stale_jobs(
    conn: sqlite3.Connection,
    *,
    board: str,
    source: str,
    company: str,
    now: str,
) -> int:
    """Mark a company's postings closed after two consecutive successful fetches of
    *that company's board* without seeing them.

    Scoped to one board because a fetch failure must never look like a wave of closures
    (PLAN §5). `board` is the `ingest_runs` key; `source`/`company` select the rows.
    Returns the number of rows closed.
    """
    runs = conn.execute(
        "SELECT run_at FROM ingest_runs WHERE source = ? AND ok = 1 ORDER BY run_at DESC LIMIT 2",
        (board,),
    ).fetchall()
    if len(runs) < 2:
        return 0  # Not enough history to distinguish "gone" from "never seen yet".

    previous_run_at = runs[1]["run_at"]
    cur = conn.execute(
        "UPDATE jobs SET is_active = 0, closed_at = ?"
        " WHERE source = ? AND company = ? AND is_active = 1 AND last_seen < ?",
        (now, source, company, previous_run_at),
    )
    return cur.rowcount

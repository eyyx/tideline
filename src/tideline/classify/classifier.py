"""LLM classification: Batch API main path, synchronous fallback, idempotent throughout.

Cost shape (PLAN §7). The Batch API halves token prices and completes well inside a
daily cron's tolerance, so it is the default path. Small daily volumes don't justify the
round trip, so anything under `SYNC_THRESHOLD` runs synchronously instead.

Idempotency is layered:
  - `unclassified_jobs()` skips jobs that already have a classification row;
  - it also skips jobs sitting in an uncollected batch, so a rerun before collection
    cannot resubmit and pay twice;
  - `insert_classification()` is INSERT OR IGNORE, so a double-collect is harmless.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

from tideline.classify.prompts import SYSTEM_PROMPT, build_user_content
from tideline.db import (
    close_batch,
    insert_classification,
    open_batches,
    record_batch,
    unclassified_jobs,
    utcnow_iso,
)
from tideline.models import Classification
from tideline.taxonomy import BY_SLUG, SLUGS

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from anthropic import Anthropic

log = logging.getLogger("tideline.classify")

#: Pinned by PLAN §7. The user chose the cheap model deliberately; do not upgrade it.
MODEL = "claude-haiku-4-5"

#: Below this many pending jobs, the Batch API's turnaround isn't worth the bookkeeping.
SYNC_THRESHOLD = 20

#: Classification output is small; this is generous headroom, not a target.
MAX_TOKENS = 1024


#: Keywords Pydantic emits that the structured-outputs schema compiler rejects. They stay
#: enforced client-side by the Pydantic model — dropping them here only affects what the
#: API is asked to constrain.
_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)


def _sanitize(node: Any) -> Any:
    """Strip unsupported keywords and enforce the two structural requirements:
    `additionalProperties: false`, and every property listed in `required`."""
    if isinstance(node, list):
        return [_sanitize(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned = {k: _sanitize(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYWORDS}
    if cleaned.get("type") == "object" and "properties" in cleaned:
        cleaned["additionalProperties"] = False
        # Optional fields must still appear in `required`; nullability is expressed by
        # the union type Pydantic already emits, not by omission.
        cleaned["required"] = list(cleaned["properties"])
    return cleaned


def _output_schema() -> dict[str, Any]:
    """JSON schema for structured output, derived from the Pydantic model so the schema
    and the parser can never drift apart.

    `category` is narrowed to an enum of the taxonomy slugs: the decoder then cannot
    emit a slug outside the taxonomy at all, which is a stronger guarantee than the
    runtime check in `_store` (kept as a belt-and-braces guard for the sync path).
    """
    schema = _sanitize(Classification.model_json_schema())
    schema["properties"]["category"]["enum"] = list(SLUGS)
    return schema


def _system_blocks() -> list[dict[str, Any]]:
    """System prompt as a cacheable block.

    The breakpoint sits on the only system block, so tools+system cache together. See
    prompts.py for why this prefix is deliberately over 4096 tokens.
    """
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _request_params(job: sqlite3.Row) -> dict[str, Any]:
    """Shared request body for both the batch and sync paths — identical bytes in the
    prefix either way, so both hit the same cache entry."""
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": _system_blocks(),
        "output_config": {"format": {"type": "json_schema", "schema": _output_schema()}},
        "messages": [
            {
                "role": "user",
                "content": build_user_content(
                    title=job["title"],
                    company=job["company"],
                    location=job["location_raw"],
                    description=job["description"],
                ),
            }
        ],
    }


def _store(conn: sqlite3.Connection, job_id: int, parsed: Classification) -> None:
    category = parsed.category
    if category not in BY_SLUG:
        # The model returned a slug outside the taxonomy. Structured outputs make this
        # near-impossible, but a silent miscategorisation would corrupt every chart, so
        # fail loudly rather than coercing it to `other`.
        raise ValueError(f"job {job_id}: model returned unknown category {category!r}")

    insert_classification(
        conn,
        job_id=job_id,
        category=category,
        tier=BY_SLUG[category].tier,
        seniority=parsed.seniority,
        skills=parsed.skills,
        salary_min=parsed.salary_min,
        salary_max=parsed.salary_max,
        salary_currency=parsed.salary_currency,
        visa_sponsorship=parsed.visa_sponsorship,
        confidence=parsed.confidence,
        model=MODEL,
        classified_at=utcnow_iso(),
    )


def collect_finished_batches(conn: sqlite3.Connection, client: Anthropic) -> int:
    """Collect results for any batch that has ended. Returns rows written.

    Batches still processing are left alone and retried on the next run.
    """
    written = 0
    for row in open_batches(conn):
        batch_id = row["batch_id"]
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            log.info(
                "batch %s still %s (%d jobs)", batch_id, batch.processing_status, row["job_count"]
            )
            continue

        errors = 0
        for result in client.messages.batches.results(batch_id):
            job_id = int(result.custom_id)
            if result.result.type != "succeeded":
                errors += 1
                log.warning("batch %s job %s: %s", batch_id, job_id, result.result.type)
                continue
            text = next((b.text for b in result.result.message.content if b.type == "text"), None)
            if text is None:
                errors += 1
                continue
            _store(conn, job_id, Classification.model_validate_json(text))
            written += 1

        close_batch(
            conn,
            batch_id=batch_id,
            collected_at=utcnow_iso(),
            error=f"{errors} failed results" if errors else None,
        )
        conn.commit()
        log.info("batch %s collected: %d classified, %d errors", batch_id, written, errors)

    return written


def submit_batch(conn: sqlite3.Connection, client: Anthropic, jobs: list[sqlite3.Row]) -> str:
    """Submit pending jobs as one batch. `custom_id` is the job id, per PLAN §7."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests = [
        Request(
            custom_id=str(job["id"]),
            params=MessageCreateParamsNonStreaming(**_request_params(job)),
        )
        for job in jobs
    ]
    batch = client.messages.batches.create(requests=requests)
    record_batch(
        conn,
        batch_id=batch.id,
        job_ids=[int(job["id"]) for job in jobs],
        submitted_at=utcnow_iso(),
    )
    conn.commit()
    log.info("submitted batch %s with %d jobs", batch.id, len(jobs))
    return batch.id


def classify_sync(conn: sqlite3.Connection, client: Anthropic, jobs: list[sqlite3.Row]) -> int:
    """Classify synchronously. Used for small daily volumes and by the eval harness."""
    written = 0
    for job in jobs:
        params = _request_params(job)
        params.pop("output_config")
        response = client.messages.parse(output_format=Classification, **params)
        _store(conn, int(job["id"]), response.parsed_output)
        written += 1
    conn.commit()
    return written


def run_classification(
    conn: sqlite3.Connection,
    client: Anthropic,
    *,
    limit: int | None = None,
    force_sync: bool = False,
) -> dict[str, int]:
    """One classification pass: collect what's ready, then dispatch what's pending."""
    collected = collect_finished_batches(conn, client)

    pending = unclassified_jobs(conn, limit=limit)
    if not pending:
        return {"collected": collected, "submitted": 0, "classified_sync": 0}

    if force_sync or len(pending) < SYNC_THRESHOLD:
        return {
            "collected": collected,
            "submitted": 0,
            "classified_sync": classify_sync(conn, client, pending),
        }

    submit_batch(conn, client, pending)
    return {"collected": collected, "submitted": len(pending), "classified_sync": 0}


def estimate_cost(
    client: Anthropic, jobs: list[sqlite3.Row], *, batch_discount: bool = True
) -> dict[str, float]:
    """Measure real token counts on a sample and project the full backfill cost.

    Uses the count_tokens endpoint rather than a character heuristic — Claude's tokenizer
    is not approximable by chars/4, and the whole point of the cached-prefix design is
    that the numbers be trustworthy.
    """
    if not jobs:
        return {}

    system_only = client.messages.count_tokens(
        model=MODEL, system=SYSTEM_PROMPT, messages=[{"role": "user", "content": "x"}]
    ).input_tokens

    totals = 0
    for job in jobs:
        params = _request_params(job)
        totals += client.messages.count_tokens(
            model=MODEL,
            system=SYSTEM_PROMPT,
            messages=params["messages"],
        ).input_tokens

    mean_total = totals / len(jobs)
    mean_job_only = max(mean_total - system_only, 0)

    # Haiku 4.5 list pricing, per million tokens.
    in_rate, out_rate = 1.0, 5.0
    multiplier = 0.5 if batch_discount else 1.0
    cached_read_rate = in_rate * 0.1
    est_output_tokens = 150

    per_job = (
        system_only / 1e6 * cached_read_rate
        + mean_job_only / 1e6 * in_rate
        + est_output_tokens / 1e6 * out_rate
    ) * multiplier

    return {
        "system_prompt_tokens": system_only,
        "mean_job_tokens": mean_job_only,
        "cache_eligible": float(system_only >= 4096),
        "cost_per_job": per_job,
    }

"""Greenhouse job board adapter (public, no auth). PLAN §6.1."""

from __future__ import annotations

from typing import Any

import httpx

from tideline.ingest.base import CompanySpec, IngestError, get_json, to_utc_iso
from tideline.ingest.locations import (
    parse_country,
    parse_subregion,
    parse_workplace_type,
)
from tideline.ingest.textutils import html_to_text
from tideline.models import NormalizedJob

API_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def parse_jobs(payload: Any, company: CompanySpec) -> list[NormalizedJob]:
    """Map a Greenhouse board payload to normalized jobs. Pure — no network."""
    if not isinstance(payload, dict) or "jobs" not in payload:
        raise IngestError(f"greenhouse/{company.token}: unexpected payload shape")

    jobs: list[NormalizedJob] = []
    for item in payload["jobs"]:
        location_raw = (item.get("location") or {}).get("name")
        country = parse_country(location_raw)
        # Greenhouse exposes no workplace field, so this is text inference only and
        # will often land on "unknown". That is the honest answer.
        workplace = parse_workplace_type(location_raw)
        jobs.append(
            NormalizedJob(
                source="greenhouse",
                source_job_id=item["id"],
                company=company.name,
                title=item["title"],
                location_raw=location_raw,
                country=country,
                subregion=parse_subregion(location_raw, country),
                workplace_type=workplace,
                is_remote=workplace == "remote",
                url=item.get("absolute_url"),
                description=html_to_text(item.get("content")),
                # first_published is a genuine publish date and is populated on every
                # posting observed; updated_at is only an approximation, so it is the
                # fallback rather than the primary (refines PLAN §6.1).
                posted_at=to_utc_iso(item.get("first_published") or item.get("updated_at")),
            )
        )
    return jobs


def fetch(company: CompanySpec, client: httpx.Client) -> list[NormalizedJob]:
    payload = get_json(client, API_URL.format(token=company.token), params={"content": "true"})
    return parse_jobs(payload, company)

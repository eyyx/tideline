"""Lever postings adapter (public, no auth). PLAN §6.2."""

from __future__ import annotations

from typing import Any

import httpx

from tideline.ingest.base import CompanySpec, IngestError, get_json, to_utc_iso
from tideline.ingest.locations import country_from_iso, is_remote, parse_country
from tideline.ingest.textutils import html_to_text
from tideline.models import NormalizedJob

API_URL = "https://api.lever.co/v0/postings/{site}"


def parse_jobs(payload: Any, company: CompanySpec) -> list[NormalizedJob]:
    """Map a Lever postings payload to normalized jobs. Pure — no network."""
    if not isinstance(payload, list):
        raise IngestError(f"lever/{company.token}: expected a list of postings")

    jobs: list[NormalizedJob] = []
    for item in payload:
        location_raw = (item.get("categories") or {}).get("location")
        # descriptionPlain is Lever's own plain-text rendering; fall back to the HTML
        # body only when it is absent.
        description = item.get("descriptionPlain") or html_to_text(item.get("description"))
        # Lever publishes an ISO country code per posting — the employer's own answer,
        # so prefer it over parsing text like "North America" or "Remote".
        country = country_from_iso(item.get("country")) or parse_country(location_raw)
        jobs.append(
            NormalizedJob(
                source="lever",
                source_job_id=item["id"],
                company=company.name,
                title=item["text"],
                location_raw=location_raw,
                country=country,
                is_remote=item.get("workplaceType") == "remote" or is_remote(location_raw),
                url=item.get("hostedUrl"),
                description=description,
                posted_at=to_utc_iso(item.get("createdAt")),
            )
        )
    return jobs


def fetch(company: CompanySpec, client: httpx.Client) -> list[NormalizedJob]:
    payload = get_json(client, API_URL.format(site=company.token), params={"mode": "json"})
    return parse_jobs(payload, company)

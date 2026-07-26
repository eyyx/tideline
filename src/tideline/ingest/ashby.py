"""Ashby job board adapter (public, no auth). PLAN §6.3."""

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

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{org}"


def _extract_compensation(item: dict[str, Any]) -> tuple[float | None, float | None, str | None]:
    """Pull a salary range out of Ashby's compensation block, if one is published.

    The shape varies by customer configuration, so this is deliberately defensive: any
    unexpected structure yields no salary rather than a wrong one. Capturing it here
    saves the classifier from re-deriving what the employer already stated (PLAN §6.3).
    """
    compensation = item.get("compensation")
    if not isinstance(compensation, dict):
        return None, None, None

    components: list[dict[str, Any]] = []
    for tier in compensation.get("compensationTiers") or []:
        if isinstance(tier, dict):
            components.extend(c for c in (tier.get("components") or []) if isinstance(c, dict))
    components.extend(
        c for c in (compensation.get("summaryComponents") or []) if isinstance(c, dict)
    )

    for component in components:
        if component.get("compensationType") != "Salary":
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        if isinstance(min_value, int | float) or isinstance(max_value, int | float):
            return (
                float(min_value) if isinstance(min_value, int | float) else None,
                float(max_value) if isinstance(max_value, int | float) else None,
                currency if isinstance(currency, str) else None,
            )
    return None, None, None


def parse_jobs(payload: Any, company: CompanySpec) -> list[NormalizedJob]:
    """Map an Ashby board payload to normalized jobs. Pure — no network."""
    if not isinstance(payload, dict) or "jobs" not in payload:
        raise IngestError(f"ashby/{company.token}: unexpected payload shape")

    jobs: list[NormalizedJob] = []
    for item in payload["jobs"]:
        if not item.get("isListed", True):
            continue  # Unlisted postings are not public openings.

        location_raw = item.get("location")
        country = parse_country(location_raw)
        workplace = parse_workplace_type(location_raw, item.get("workplaceType"))
        salary_min, salary_max, currency = _extract_compensation(item)
        jobs.append(
            NormalizedJob(
                source="ashby",
                source_job_id=item["id"],
                company=company.name,
                title=item["title"],
                location_raw=location_raw,
                country=country,
                subregion=parse_subregion(location_raw, country),
                workplace_type=workplace,
                # Ashby sets isRemote=true on Hybrid postings, so it cannot define
                # "remote"; derive the boolean from the typed field instead.
                is_remote=workplace == "remote",
                url=item.get("jobUrl"),
                # Ashby ships its own plain-text rendering; only convert the HTML when
                # that is missing.
                description=item.get("descriptionPlain")
                or html_to_text(item.get("descriptionHtml")),
                posted_at=to_utc_iso(item.get("publishedAt") or item.get("updatedAt")),
                salary_min=salary_min,
                salary_max=salary_max,
                salary_currency=currency,
            )
        )
    return jobs


def fetch(company: CompanySpec, client: httpx.Client) -> list[NormalizedJob]:
    payload = get_json(
        client, API_URL.format(org=company.token), params={"includeCompensation": "true"}
    )
    return parse_jobs(payload, company)

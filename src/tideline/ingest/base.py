"""Adapter protocol and shared HTTP plumbing.

Adapters are pure functions: config in, `list[NormalizedJob]` out, no DB access. Network
and payload problems raise `IngestError` for the orchestrator to catch and log, so one
failing company or source can never take down a run (PLAN §13).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
import yaml
from pydantic import BaseModel, Field

from tideline.models import NormalizedJob

DEFAULT_COMPANIES_PATH = Path("config/companies.yaml")
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
USER_AGENT = "tideline/0.1 (job market research; contact via repo)"


class IngestError(Exception):
    """Any failure to retrieve or parse a source's postings."""


class CompanySpec(BaseModel):
    """One entry in `config/companies.yaml`."""

    name: str
    ats: Literal["greenhouse", "lever", "ashby"]
    token: str = Field(description="board_token (Greenhouse) / site (Lever) / org (Ashby).")
    note: str | None = None


class Adapter(Protocol):
    def __call__(self, company: CompanySpec, client: httpx.Client) -> list[NormalizedJob]: ...


def load_companies(path: Path | str = DEFAULT_COMPANIES_PATH) -> list[CompanySpec]:
    """Read and validate the company list. Commented-out entries are simply absent."""
    path = Path(path)
    if not path.exists():
        raise IngestError(f"Company list not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise IngestError(f"{path} must contain a YAML list of companies")
    return [CompanySpec.model_validate(entry) for entry in raw]


def to_utc_iso(value: str | int | float | None) -> str | None:
    """Normalize a source timestamp to a UTC ISO 8601 string.

    Accepts ISO strings (Greenhouse, Ashby) and epoch milliseconds (Lever). Returns None
    for anything unparseable — a missing `posted_at` is acceptable, a wrong one is not.
    """
    if value is None or value == "":
        return None

    if isinstance(value, int | float):
        # Lever reports epoch milliseconds.
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC).replace(microsecond=0).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat()


def make_client() -> httpx.Client:
    """Shared HTTP client. Identifies itself — these are public APIs used politely."""
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


def get_json(client: httpx.Client, url: str, params: dict[str, Any] | None = None) -> Any:
    """GET returning parsed JSON, converting every failure mode into `IngestError`."""
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise IngestError(f"{url} returned HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise IngestError(f"{url} request failed: {exc}") from exc
    except ValueError as exc:
        raise IngestError(f"{url} returned invalid JSON: {exc}") from exc

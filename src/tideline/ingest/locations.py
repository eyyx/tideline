"""Rule-based parsing of free-text location strings into SG / US / AU / OTHER.

Country assignment happens in the adapter (PLAN §5), before the LLM ever sees the job,
because region is a filter dimension on every market-layer chart and must not depend on
a billable call.

Multi-location postings ("Sydney, Australia; New York, NY") are resolved against the
*first* segment only. A posting can carry one country in this schema, and the first
listed location is the primary one in practice.
"""

from __future__ import annotations

import re

Country = str  # "SG" | "US" | "AU" | "OTHER"

_SEGMENT_SPLIT = re.compile(r"[;|/]|\bor\b|\band\b")

_US_STATES = {
    "al",
    "ak",
    "az",
    "ar",
    "ca",
    "co",
    "ct",
    "de",
    "fl",
    "ga",
    "hi",
    "id",
    "il",
    "in",
    "ia",
    "ks",
    "ky",
    "la",
    "me",
    "md",
    "ma",
    "mi",
    "mn",
    "ms",
    "mo",
    "mt",
    "ne",
    "nv",
    "nh",
    "nj",
    "nm",
    "ny",
    "nc",
    "nd",
    "oh",
    "ok",
    "or",
    "pa",
    "ri",
    "sc",
    "sd",
    "tn",
    "tx",
    "ut",
    "vt",
    "va",
    "wa",
    "wv",
    "wi",
    "wy",
    "dc",
}

_AU_CITIES = {
    "sydney",
    "melbourne",
    "brisbane",
    "perth",
    "adelaide",
    "canberra",
    "hobart",
    "darwin",
    "gold coast",
    "newcastle",
    "wollongong",
}

_US_CITIES = {
    "san francisco",
    "sf",
    "new york",
    "nyc",
    "brooklyn",
    "seattle",
    "austin",
    "boston",
    "chicago",
    "los angeles",
    "denver",
    "atlanta",
    "san jose",
    "palo alto",
    "menlo park",
    "mountain view",
    "sunnyvale",
    "santa clara",
    "cupertino",
    "redmond",
    "bellevue",
    "cambridge",
    "washington",
    "philadelphia",
    "san diego",
    "portland",
    "miami",
    "dallas",
    "houston",
    "phoenix",
    "detroit",
    "minneapolis",
    "pittsburgh",
    "raleigh",
    "durham",
    "boulder",
    "salt lake city",
    "nashville",
    "bay area",
    "silicon valley",
}

_REMOTE_MARKERS = ("remote", "work from home", "wfh", "anywhere", "distributed", "virtual")

_WORD = re.compile(r"[a-z]+")


#: Sources that publish a real ISO country code (Lever) should use it instead of
#: guessing from text — it is the employer's own answer.
_ISO_TO_COUNTRY = {"SG": "SG", "US": "US", "AU": "AU"}


def country_from_iso(code: str | None) -> Country | None:
    """Map an ISO 3166-1 alpha-2 code to our country bucket.

    Returns None when the source gave us nothing, so the caller can fall back to text
    parsing; returns "OTHER" for a real code outside our three target regions.
    """
    if not code or not isinstance(code, str):
        return None
    return _ISO_TO_COUNTRY.get(code.strip().upper(), "OTHER")


def is_remote(location_raw: str | None) -> bool:
    if not location_raw:
        return False
    return any(marker in location_raw.casefold() for marker in _REMOTE_MARKERS)


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def parse_country(location_raw: str | None) -> Country:
    """Best-effort country for a free-text location string."""
    if not location_raw:
        return "OTHER"

    text = location_raw.casefold().replace(".", "")
    segment = _SEGMENT_SPLIT.split(text)[0].strip() or text

    # 1. Explicit country names win outright.
    if _has_word(segment, "singapore"):
        return "SG"
    if _has_word(segment, "australia"):
        return "AU"
    if any(_has_word(segment, w) for w in ("united states", "usa", "us", "u s", "america")):
        return "US"

    # 2. A US state code is a strong signal, and disambiguates cities that exist in both
    #    countries ("Melbourne, FL" is Florida, not Victoria).
    tokens = {t.strip() for t in segment.split(",")}
    if tokens & _US_STATES:
        return "US"

    # 3. Fall back to city names.
    if any(city in segment for city in _AU_CITIES):
        return "AU"
    if any(city in segment for city in _US_CITIES):
        return "US"

    return "OTHER"

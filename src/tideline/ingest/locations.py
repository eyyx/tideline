"""Rule-based parsing of free-text location strings into SG / US / AU / NL / OTHER,
plus US sub-region and workplace type.

Country assignment happens in the adapter (PLAN §5), before the LLM ever sees the job,
because region is a filter dimension on every market-layer chart and must not depend on
a billable call.

Multi-location postings are resolved by *target priority*, not position — see
`parse_country`. Sub-region is US-state granularity only: "the US" is not a market for
this purpose, CA / NY / MA are.
"""

from __future__ import annotations

import re

Country = str  # "SG" | "US" | "AU" | "NL" | "OTHER"

#: Separators that genuinely delimit multiple locations. Deliberately excludes "/", which
#: appears inside single location names ("Headquarters/Sunnyvale Office") far more often
#: than it separates two of them.
_SEGMENT_SPLIT = re.compile(r"[;|]|\bor\b|\band\b")

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

_NL_CITIES = {
    "amsterdam",
    "rotterdam",
    "utrecht",
    "the hague",
    "den haag",
    "eindhoven",
    "groningen",
    "delft",
    "haarlem",
    "leiden",
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
_ISO_TO_COUNTRY = {"SG": "SG", "US": "US", "AU": "AU", "NL": "NL"}


def country_from_iso(code: str | None) -> Country | None:
    """Map an ISO 3166-1 alpha-2 code to our country bucket.

    Returns None when the source gave us nothing, so the caller can fall back to text
    parsing; returns "OTHER" for a real code outside our four target regions.
    """
    if not code or not isinstance(code, str):
        return None
    return _ISO_TO_COUNTRY.get(code.strip().upper(), "OTHER")


#: US cities that imply a state when the posting omits it. Only the metros that matter
#: for filtering — an exhaustive gazetteer is not the goal.
_CITY_TO_STATE = {
    "san francisco": "CA",
    "sf": "CA",
    "palo alto": "CA",
    "mountain view": "CA",
    "menlo park": "CA",
    "sunnyvale": "CA",
    "santa clara": "CA",
    "cupertino": "CA",
    "san jose": "CA",
    "los angeles": "CA",
    "san diego": "CA",
    "oakland": "CA",
    "berkeley": "CA",
    "bay area": "CA",
    "silicon valley": "CA",
    "irvine": "CA",
    "new york": "NY",
    "nyc": "NY",
    "brooklyn": "NY",
    "manhattan": "NY",
    "boston": "MA",
    "cambridge": "MA",
    "somerville": "MA",
    "seattle": "WA",
    "bellevue": "WA",
    "redmond": "WA",
    "austin": "TX",
    "dallas": "TX",
    "houston": "TX",
    "chicago": "IL",
    "denver": "CO",
    "boulder": "CO",
    "atlanta": "GA",
    "miami": "FL",
    "portland": "OR",
    "philadelphia": "PA",
    "pittsburgh": "PA",
    "washington": "DC",
    "nashville": "TN",
    "phoenix": "AZ",
    "detroit": "MI",
    "minneapolis": "MN",
    "raleigh": "NC",
    "durham": "NC",
    "salt lake city": "UT",
}


def parse_subregion(location_raw: str | None, country: str) -> str | None:
    """Sub-national region. Currently US states only — the other target markets are
    small enough that country granularity is sufficient."""
    if country != "US" or not location_raw:
        return None

    text = location_raw.casefold().replace(".", "")
    for segment in (s.strip() for s in _SEGMENT_SPLIT.split(text)):
        if tokens := {t.strip() for t in segment.split(",")} & _US_STATES:
            return next(iter(tokens)).upper()
        for city, state in _CITY_TO_STATE.items():
            if city in segment:
                return state
    return None


def parse_workplace_type(location_raw: str | None, declared: str | None = None) -> str:
    """Resolve remote / hybrid / onsite.

    `declared` is the source's own typed field (Lever `workplaceType`, Ashby
    `workplaceType`) and always wins — it is the employer's answer. Only when a source
    supplies nothing do we guess from the location text, and an unguessable posting is
    `unknown` rather than being silently called onsite.
    """
    if declared:
        normalized = declared.strip().casefold().replace("-", "").replace(" ", "")
        if normalized in ("remote", "fullyremote"):
            return "remote"
        if normalized == "hybrid":
            return "hybrid"
        if normalized in ("onsite", "inoffice", "office"):
            return "onsite"

    if not location_raw:
        return "unknown"
    text = location_raw.casefold()
    if "hybrid" in text:
        return "hybrid"
    if any(marker in text for marker in _REMOTE_MARKERS):
        return "remote"
    if any(marker in text for marker in ("in-office", "in office", "onsite", "on-site")):
        return "onsite"
    return "unknown"


def is_remote(location_raw: str | None) -> bool:
    if not location_raw:
        return False
    return any(marker in location_raw.casefold() for marker in _REMOTE_MARKERS)


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def _country_of_segment(segment: str) -> Country | None:
    """Resolve one location segment, or None if it carries no recognisable signal."""
    # 1. Explicit country names win outright.
    if _has_word(segment, "singapore"):
        return "SG"
    if _has_word(segment, "australia"):
        return "AU"
    if any(_has_word(segment, w) for w in ("netherlands", "holland")):
        return "NL"
    if any(_has_word(segment, w) for w in ("united states", "usa", "us", "u s", "america")):
        return "US"

    # 2. A US state code is a strong signal, and disambiguates cities that exist in both
    #    countries ("Melbourne, FL" is Florida, not Victoria). Checked before city names
    #    so the state wins within its own segment.
    if {t.strip() for t in segment.split(",")} & _US_STATES:
        return "US"

    # 3. Fall back to city names.
    if any(city in segment for city in _AU_CITIES):
        return "AU"
    if any(city in segment for city in _NL_CITIES):
        return "NL"
    if any(city in segment for city in _US_CITIES):
        return "US"
    return None


def parse_country(location_raw: str | None) -> Country:
    """Best-effort country for a free-text location string.

    Multi-location postings ("Kuala Lumpur, Malaysia; Singapore") are resolved by target
    priority rather than by position: if *any* segment names a target region, that region
    wins, SG first. A posting open in Singapore is a Singapore posting regardless of
    where Singapore appears in the list, and an earlier position-based rule was
    systematically dropping exactly the APAC multi-location roles this system exists to
    surface.

    Disambiguation still happens *within* a segment, so "Melbourne, FL" is US (the state
    code beats the city name) while "Melbourne, Australia" is AU.
    """
    if not location_raw:
        return "OTHER"

    text = location_raw.casefold().replace(".", "")
    segments = [s.strip() for s in _SEGMENT_SPLIT.split(text) if s.strip()] or [text]

    resolved = [c for c in (_country_of_segment(s) for s in segments) if c]
    if not resolved:
        return "OTHER"

    # Target-region priority. SG is the home market and the scarcest signal, so it
    # outranks the rest; US comes last precisely because it is the most common — a
    # posting listing both Singapore and New York is far more interesting as a Singapore
    # posting. OTHER never wins over a named target.
    for country in ("SG", "AU", "NL", "US"):
        if country in resolved:
            return country
    return "OTHER"

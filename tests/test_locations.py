"""Country parsing rules. Region is a filter dimension on every market chart, so a
mis-parse skews trend lines rather than just losing one row."""

import pytest

from tideline.ingest.locations import country_from_iso, is_remote, parse_country


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Singapore", "SG"),
        ("Singapore, Singapore", "SG"),
        ("Sydney, Australia", "AU"),
        ("Melbourne, Australia", "AU"),
        ("Brisbane", "AU"),
        ("San Francisco, CA", "US"),
        ("New York, NY", "US"),
        ("San Francisco", "US"),
        ("Washington, D.C.", "US"),
        ("United States", "US"),
        ("London, United Kingdom", "OTHER"),
        ("Tokyo, Japan", "OTHER"),
        (None, "OTHER"),
        ("", "OTHER"),
    ],
)
def test_parse_country(location, expected):
    assert parse_country(location) == expected


def test_us_state_code_disambiguates_shared_city_names():
    # Melbourne exists in both Victoria and Florida; the state code must win.
    assert parse_country("Melbourne, FL") == "US"
    assert parse_country("Melbourne, Australia") == "AU"


def test_multi_location_resolves_to_first_segment():
    assert parse_country("Sydney, Australia; New York, NY") == "AU"
    assert parse_country("Singapore | London") == "SG"


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Remote", True),
        ("Remote - US", True),
        ("Work from home", True),
        ("Anywhere", True),
        ("San Francisco, CA", False),
        (None, False),
    ],
)
def test_is_remote(location, expected):
    assert is_remote(location) is expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [("US", "US"), ("sg", "SG"), (" au ", "AU"), ("GB", "OTHER"), ("", None), (None, None)],
)
def test_country_from_iso(code, expected):
    assert country_from_iso(code) == expected

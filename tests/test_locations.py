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


def test_multi_location_resolves_by_target_priority_not_position():
    """A posting open in Singapore is a Singapore posting wherever Singapore appears."""
    assert parse_country("Kuala Lumpur, Malaysia; Singapore") == "SG"
    assert parse_country("Seoul, South Korea; Singapore; Tokyo, Japan") == "SG"
    assert parse_country("Singapore | London") == "SG"
    assert parse_country("Sydney, Australia; New York, NY") == "AU"  # SG absent, AU wins
    assert parse_country("London, UK; Remote-Friendly, United States; San Francisco, CA") == "US"


def test_slash_is_not_a_location_separator():
    """'Headquarters/Sunnyvale Office' is one location, not two."""
    assert parse_country("Headquarters/Sunnyvale Office") == "US"


def test_non_target_multi_location_stays_other():
    assert parse_country("London, UK; Dublin, Ireland") == "OTHER"


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

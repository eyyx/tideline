"""Dedupe fingerprints. A false merge silently loses a real posting, so these rules
stay conservative and exact."""

import pytest

from tideline.lifecycle import dedupe_key_for, normalize, normalize_company


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Stripe, Inc.", "stripe"),
        ("Grab Holdings Pte Ltd", "grab"),
        ("Canva Pty Ltd", "canva"),
        ("Sea Limited", "sea"),
        ("ACME Corp", "acme"),
        ("Anthropic", "anthropic"),
        ("  Multi   Space  Co  ", "multi space"),
    ],
)
def test_normalize_company_strips_legal_suffixes(raw, expected):
    assert normalize_company(raw) == expected


def test_normalize_lowercases_and_drops_punctuation():
    assert normalize("Senior  Data-Scientist, ML!") == "senior data scientist ml"


def test_key_is_stable_across_cosmetic_differences():
    assert dedupe_key_for("Stripe, Inc.", "Data Scientist", "SG") == dedupe_key_for(
        "stripe", "data  scientist", "SG"
    )


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # Same role, different country — genuinely different postings.
        (("Anthropic", "Data Scientist", "SG"), ("Anthropic", "Data Scientist", "US")),
        # Seniority is part of the identity.
        (("Anthropic", "Data Scientist", "SG"), ("Anthropic", "Senior Data Scientist", "SG")),
        # Different employers.
        (("Anthropic", "Data Scientist", "SG"), ("OpenAI", "Data Scientist", "SG")),
    ],
)
def test_distinct_postings_do_not_collide(a, b):
    assert dedupe_key_for(*a) != dedupe_key_for(*b)


def test_company_name_containing_a_suffix_word_is_preserved():
    """Only trailing legal suffixes are stripped, never interior words."""
    assert normalize_company("Incorporated Labs") == "incorporated labs"
    assert normalize_company("Cohere") == "cohere"

"""Adapter parsing tests — offline, against trimmed copies of real ATS payloads."""

import pytest

from tideline.ingest import ashby, greenhouse, lever
from tideline.ingest.base import CompanySpec, IngestError


@pytest.fixture
def gh_company():
    return CompanySpec(name="Anthropic", ats="greenhouse", token="anthropic")


@pytest.fixture
def lever_company():
    return CompanySpec(name="Palantir", ats="lever", token="palantir")


@pytest.fixture
def ashby_company():
    return CompanySpec(name="OpenAI", ats="ashby", token="openai")


class TestGreenhouse:
    def test_maps_core_fields(self, load_fixture, gh_company):
        jobs = greenhouse.parse_jobs(load_fixture("greenhouse_jobs.json"), gh_company)

        assert len(jobs) == 4
        job = next(j for j in jobs if j.location_raw == "Singapore")
        assert job.source == "greenhouse"
        assert job.company == "Anthropic"
        assert job.country == "SG"
        assert job.url.startswith("https://")
        assert job.source_job_id.isdigit()  # ints coerced to str

    def test_assigns_countries_across_regions(self, load_fixture, gh_company):
        jobs = greenhouse.parse_jobs(load_fixture("greenhouse_jobs.json"), gh_company)
        by_location = {j.location_raw: j.country for j in jobs}

        assert by_location["Sydney, Australia"] == "AU"
        assert by_location["Singapore"] == "SG"
        assert by_location["San Francisco, CA"] == "US"
        assert by_location["London, UK"] == "OTHER"

    def test_description_converted_to_plain_text(self, load_fixture, gh_company):
        jobs = greenhouse.parse_jobs(load_fixture("greenhouse_jobs.json"), gh_company)

        # Greenhouse double-encodes HTML; neither raw nor escaped markup may survive.
        for job in jobs:
            assert "&lt;" not in job.description
            assert "<div" not in job.description

    def test_posted_at_is_utc(self, load_fixture, gh_company):
        jobs = greenhouse.parse_jobs(load_fixture("greenhouse_jobs.json"), gh_company)
        assert all(j.posted_at.endswith("+00:00") for j in jobs)

    def test_rejects_unexpected_payload(self, gh_company):
        with pytest.raises(IngestError):
            greenhouse.parse_jobs({"nope": []}, gh_company)


class TestLever:
    def test_prefers_declared_iso_country(self, load_fixture, lever_company):
        jobs = lever.parse_jobs(load_fixture("lever_postings.json"), lever_company)
        by_country = {j.country for j in jobs}

        assert {"US", "AU", "SG"} <= by_country
        assert "OTHER" in by_country  # the GB posting

    def test_maps_core_fields(self, load_fixture, lever_company):
        jobs = lever.parse_jobs(load_fixture("lever_postings.json"), lever_company)
        job = next(j for j in jobs if j.country == "SG")

        assert job.source == "lever"
        assert job.company == "Palantir"
        assert job.title
        assert job.description
        # createdAt arrives as epoch milliseconds.
        assert job.posted_at.startswith("20") and job.posted_at.endswith("+00:00")

    def test_rejects_non_list_payload(self, lever_company):
        with pytest.raises(IngestError):
            lever.parse_jobs({"ok": False, "error": "Document not found"}, lever_company)


class TestAshby:
    def test_skips_unlisted_postings(self, load_fixture, ashby_company):
        payload = load_fixture("ashby_board.json")
        jobs = ashby.parse_jobs(payload, ashby_company)

        assert len(payload["jobs"]) == 3
        assert len(jobs) == 2
        assert all(j.source_job_id != "unlisted-synthetic" for j in jobs)

    def test_extracts_published_salary(self, load_fixture, ashby_company):
        jobs = ashby.parse_jobs(load_fixture("ashby_board.json"), ashby_company)
        with_salary = [j for j in jobs if j.salary_min is not None]

        assert len(with_salary) == 1
        job = with_salary[0]
        assert job.salary_min > 0
        assert job.salary_max >= job.salary_min
        assert job.salary_currency == "USD"

    def test_absent_compensation_yields_no_salary(self, load_fixture, ashby_company):
        jobs = ashby.parse_jobs(load_fixture("ashby_board.json"), ashby_company)
        without = [j for j in jobs if j.salary_currency is None]

        assert without and all(j.salary_min is None and j.salary_max is None for j in without)

    def test_malformed_compensation_is_ignored_not_fatal(self, load_fixture, ashby_company):
        payload = load_fixture("ashby_board.json")
        payload["jobs"][0]["compensation"] = {"compensationTiers": "not-a-list"}

        jobs = ashby.parse_jobs(payload, ashby_company)
        assert jobs[0].salary_min is None

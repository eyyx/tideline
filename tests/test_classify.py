"""Classification: prompt stability, schema shape, and idempotency.

No network. The Anthropic client is faked, because what needs testing here is our
bookkeeping — that a rerun never pays for the same job twice — not the model's answers.
"""

import json

import pytest

from tideline.classify import classifier
from tideline.classify.prompts import SYSTEM_PROMPT, build_system_prompt, build_user_content
from tideline.db import connect, init_schema, unclassified_jobs, upsert_job
from tideline.models import NormalizedJob

NOW = "2026-07-26T00:00:00+00:00"

#: Haiku 4.5's minimum cacheable prefix. Below this, caching silently never engages.
CACHE_FLOOR_TOKENS = 4096
#: Conservative chars-per-token for English prose. A real count needs the API; this is a
#: regression guard, not a measurement.
CHARS_PER_TOKEN = 4.0


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def add_job(conn, source_job_id: str, title: str = "Data Scientist") -> int:
    upsert_job(
        conn,
        NormalizedJob(
            source="greenhouse",
            source_job_id=source_job_id,
            company="Anthropic",
            title=title,
            location_raw="Singapore",
            country="SG",
            description="Build models.",
        ),
        NOW,
    )
    return conn.execute("SELECT id FROM jobs WHERE source_job_id = ?", (source_job_id,)).fetchone()[
        "id"
    ]


class TestPrompt:
    def test_is_deterministic(self):
        """The cached prefix must be byte-identical across calls, or caching never hits."""
        assert build_system_prompt() == build_system_prompt() == SYSTEM_PROMPT

    def test_clears_the_cache_floor(self):
        """Guards the economics: under 4096 tokens, cache_control is a silent no-op and
        every job re-pays for the full system prompt."""
        approx_tokens = len(SYSTEM_PROMPT) / CHARS_PER_TOKEN
        assert approx_tokens >= CACHE_FLOOR_TOKENS, (
            f"system prompt ~{approx_tokens:.0f} tokens, below the {CACHE_FLOOR_TOKENS} "
            "floor for claude-haiku-4-5"
        )

    def test_contains_every_taxonomy_slug(self):
        from tideline.taxonomy import SLUGS

        for slug in SLUGS:
            assert slug in SYSTEM_PROMPT

    def test_carries_no_volatile_content(self):
        """A date or counter in the prefix would invalidate the cache every run."""
        assert "2026" not in SYSTEM_PROMPT.replace("2026-", "")

    def test_user_content_truncates_description(self):
        content = build_user_content(
            title="X", company="Y", location=None, description="z" * 10_000
        )
        assert len(content) < 5_000
        assert "LOCATION: not stated" in content


class TestOutputSchema:
    def test_strips_unsupported_numeric_constraints(self):
        """`confidence` has ge/le on the model; structured outputs rejects those."""
        schema = json.dumps(classifier._output_schema())
        assert "minimum" not in schema
        assert "maximum" not in schema

    def test_objects_are_closed_and_fully_required(self):
        schema = classifier._output_schema()
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])

    def test_keeps_the_category_field(self):
        assert "category" in classifier._output_schema()["properties"]


class TestIdempotency:
    def test_unclassified_excludes_already_classified(self, conn):
        job_id = add_job(conn, "1")
        assert [r["id"] for r in unclassified_jobs(conn)] == [job_id]

        conn.execute(
            "INSERT INTO classifications (job_id, category, tier, model, classified_at)"
            " VALUES (?, 'data_scientist', 1, 'claude-haiku-4-5', ?)",
            (job_id, NOW),
        )
        assert unclassified_jobs(conn) == []

    def test_unclassified_excludes_jobs_in_an_open_batch(self, conn):
        """The core double-billing guard: a rerun before collection must not resubmit."""
        job_id = add_job(conn, "1")
        conn.execute(
            "INSERT INTO classify_batches (batch_id, submitted_at, job_ids, job_count)"
            " VALUES ('batch_1', ?, ?, 1)",
            (NOW, json.dumps([job_id])),
        )
        assert unclassified_jobs(conn) == []

    def test_collected_batch_stops_shielding_its_jobs(self, conn):
        """Once collected, a job that somehow got no classification becomes eligible
        again rather than being stranded forever."""
        job_id = add_job(conn, "1")
        conn.execute(
            "INSERT INTO classify_batches"
            " (batch_id, submitted_at, job_ids, job_count, collected_at)"
            " VALUES ('batch_1', ?, ?, 1, ?)",
            (NOW, json.dumps([job_id]), NOW),
        )
        assert [r["id"] for r in unclassified_jobs(conn)] == [job_id]

    def test_store_rejects_a_category_outside_the_taxonomy(self, conn):
        from tideline.models import Classification

        job_id = add_job(conn, "1")
        bogus = Classification(category="prompt_engineer", confidence=0.9)

        with pytest.raises(ValueError, match="unknown category"):
            classifier._store(conn, job_id, bogus)

    def test_store_is_safe_to_repeat(self, conn):
        from tideline.models import Classification

        job_id = add_job(conn, "1")
        parsed = Classification(category="data_scientist", confidence=0.9, skills=["python"])

        classifier._store(conn, job_id, parsed)
        classifier._store(conn, job_id, parsed)  # double-collect must not raise or dupe

        rows = conn.execute("SELECT tier, skills FROM classifications").fetchall()
        assert len(rows) == 1
        assert rows[0]["tier"] == 1
        assert json.loads(rows[0]["skills"]) == ["python"]


class TestDispatch:
    def test_small_batches_go_synchronous(self, conn, monkeypatch):
        """Under the threshold, the batch round trip isn't worth it."""
        for i in range(3):
            add_job(conn, str(i))

        calls = {"sync": 0, "batch": 0}

        def fake_sync(c, cl, jobs):
            calls["sync"] = len(jobs)
            return len(jobs)

        def fake_batch(c, cl, jobs):
            calls["batch"] = len(jobs)
            return "batch_x"

        monkeypatch.setattr(classifier, "classify_sync", fake_sync)
        monkeypatch.setattr(classifier, "submit_batch", fake_batch)
        monkeypatch.setattr(classifier, "collect_finished_batches", lambda c, cl: 0)

        result = classifier.run_classification(conn, client=None)
        assert calls == {"sync": 3, "batch": 0}
        assert result["classified_sync"] == 3

    def test_large_volumes_go_to_the_batch_api(self, conn, monkeypatch):
        for i in range(classifier.SYNC_THRESHOLD + 5):
            add_job(conn, str(i))

        seen = {}
        monkeypatch.setattr(
            classifier, "submit_batch", lambda c, cl, jobs: seen.setdefault("n", len(jobs))
        )
        monkeypatch.setattr(classifier, "collect_finished_batches", lambda c, cl: 0)

        result = classifier.run_classification(conn, client=None)
        assert seen["n"] == classifier.SYNC_THRESHOLD + 5
        assert result["submitted"] == classifier.SYNC_THRESHOLD + 5

    def test_nothing_pending_is_a_no_op(self, conn, monkeypatch):
        monkeypatch.setattr(classifier, "collect_finished_batches", lambda c, cl: 0)
        assert classifier.run_classification(conn, client=None) == {
            "collected": 0,
            "submitted": 0,
            "classified_sync": 0,
        }

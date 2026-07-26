"""HTML-to-text conversion for job descriptions."""

from tideline.ingest.textutils import html_to_text
from tideline.models import DESCRIPTION_MAX_CHARS, NormalizedJob


def test_strips_tags_and_keeps_text():
    assert html_to_text("<p>Hello <strong>world</strong></p>") == "Hello world"


def test_handles_double_encoded_markup():
    """Greenhouse ships escaped HTML rather than HTML."""
    result = html_to_text("&lt;p&gt;About &lt;strong&gt;Anthropic&lt;/strong&gt;&lt;/p&gt;")
    assert result == "About Anthropic"
    assert "&lt;" not in result


def test_list_items_become_bullets():
    result = html_to_text("<ul><li>Python</li><li>SQL</li></ul>")
    assert "- Python" in result
    assert "- SQL" in result


def test_drops_script_and_style_content():
    result = html_to_text("<div>Real<script>var x=1;</script><style>.a{}</style></div>")
    assert "var x" not in result
    assert "Real" in result


def test_collapses_excess_blank_lines():
    assert "\n\n\n" not in html_to_text("<p>a</p><div></div><div></div><p>b</p>")


def test_entities_are_decoded():
    assert html_to_text("<p>R&amp;D at Anthropic&#8217;s lab</p>") == "R&D at Anthropic’s lab"


def test_empty_input_yields_none():
    assert html_to_text(None) is None
    assert html_to_text("") is None
    assert html_to_text("<div>  </div>") is None


def test_description_is_truncated_at_the_model_boundary():
    job = NormalizedJob(
        source="greenhouse",
        source_job_id="1",
        company="Anthropic",
        title="Data Scientist",
        description="x" * (DESCRIPTION_MAX_CHARS + 5000),
    )
    assert len(job.description) == DESCRIPTION_MAX_CHARS

"""Role taxonomy — the single definition of categories, tiers, and boundary rules.

Shared by the classification prompt (Phase 2) and the dashboard (Phase 4), so the
labels in the UI can never drift from the labels the model was asked to produce.

Tier semantics (PLAN §3):
  1 = target roles      — alert-eligible, shown in the job layer
  2 = market signal     — market-layer stats only, never alerted
  3 = everything else
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Category:
    slug: str
    tier: int
    label: str
    hint: str
    """Decision guidance shown to the classifier; keep it terse and behavioural."""


CATEGORIES: Final[tuple[Category, ...]] = (
    Category(
        slug="data_scientist",
        tier=1,
        label="Data Scientist",
        hint="DS, Applied Scientist, Decision Scientist.",
    ),
    Category(
        slug="ai_engineer",
        tier=1,
        label="AI Engineer",
        hint="AI Engineer, LLM Engineer, GenAI Engineer; application-layer LLM development.",
    ),
    Category(
        slug="ml_engineer",
        tier=1,
        label="ML Engineer",
        hint=(
            "MLE, ML Platform, ML Infra. Includes engineering-leaning Research Engineer "
            "(builds systems rather than publishing)."
        ),
    ),
    Category(
        slug="agentic_engineer",
        tier=1,
        label="Agentic Engineer",
        hint=(
            "Agent Engineer, Agentic Engineer, AI agent development. Emerging title — "
            "prefer it over ai_engineer when the JD centres on agents, tool use, or "
            "multi-step autonomy."
        ),
    ),
    Category(
        slug="forward_deployed_engineer",
        tier=1,
        label="Forward Deployed Engineer",
        hint=(
            "FDE, Forward Deployed SWE. Includes Solutions Engineer/Architect only when "
            "the JD clearly describes customer-embedded or on-site delivery work."
        ),
    ),
    Category(
        slug="engineering_analyst",
        tier=1,
        label="Engineering Analyst",
        hint=(
            "Engineering Analyst, Analytics Engineer, and technically-leaning "
            "Product/Business Analyst (dbt, warehouse modelling, pipelines)."
        ),
    ),
    Category(
        slug="data_analyst",
        tier=1,
        label="Data Analyst",
        hint="Classic BI/reporting analyst work: SQL, dashboards, stakeholder reporting.",
    ),
    Category(
        slug="software_developer",
        tier=2,
        label="Software Developer",
        hint=(
            "Generic SWE: frontend, backend, full-stack, mobile, infra. Not a target role — "
            "this is the industry baseline for overall tech-hiring temperature."
        ),
    ),
    Category(
        slug="other",
        tier=3,
        label="Other",
        hint="None of the above: PM, design, sales, ops, and research-scientist roles.",
    ),
)

BY_SLUG: Final[dict[str, Category]] = {c.slug: c for c in CATEGORIES}
SLUGS: Final[tuple[str, ...]] = tuple(c.slug for c in CATEGORIES)
TIER1_SLUGS: Final[tuple[str, ...]] = tuple(c.slug for c in CATEGORIES if c.tier == 1)

# Written into the classification prompt verbatim (PLAN §3).
BOUNDARY_RULES: Final[tuple[str, ...]] = (
    'Ambiguous titles such as "Member of Technical Staff" must be decided from the job '
    "description, never from the title alone.",
    "Research Scientist (publication-oriented) is `other`; Research Engineer "
    "(engineering-oriented) is `ml_engineer`.",
    "When several categories fit, pick the most specific. Precedence: "
    "agentic_engineer > ai_engineer > ml_engineer > software_developer.",
    "Solutions Engineer defaults to `other` unless the JD clearly describes "
    "forward-deployed, on-site, or customer-embedded delivery.",
)


def tier_of(slug: str) -> int:
    """Tier for a taxonomy slug. Raises KeyError on an unknown slug (fail loud —
    an unrecognised label means the model or the prompt has drifted)."""
    return BY_SLUG[slug].tier

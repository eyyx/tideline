"""Pydantic models forming the contract between adapters, the DB, and the classifier.

Adapters are pure functions that emit `NormalizedJob`; nothing downstream should ever
see a source's raw payload shape (PLAN §13).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Target regions: Singapore (home market), the US, Australia, and the Netherlands.
#: NZ was briefly a target and was dropped — anything outside these four is OTHER.
Country = Literal["SG", "US", "AU", "NL", "OTHER"]

#: Remote / hybrid / onsite is a three-way distinction, not a boolean. Collapsing hybrid
#: into "remote" is actively wrong for relocation decisions: a hybrid role still requires
#: living in the city. Ashby reports `isRemote: true` on hybrid postings, so its boolean
#: cannot be trusted — the typed field is the source of truth where a source provides one.
WorkplaceType = Literal["remote", "hybrid", "onsite", "unknown"]
Source = Literal["greenhouse", "lever", "ashby", "adzuna"]
Seniority = Literal["intern", "junior", "mid", "senior", "staff_plus", "manager", "unknown"]
VisaSponsorship = Literal["yes", "no", "unknown"]

#: Storage cap for descriptions, applied at the model boundary so no adapter can bloat
#: the committed DB file. Set to match what the classifier actually reads (PLAN §7) —
#: storing more than that is bytes nothing ever looks at.
DESCRIPTION_MAX_CHARS = 4000

#: What survives once a job has been classified. The description is an LLM input, not an
#: analytical asset: after extraction the dashboard shows a short preview and links out
#: to `url` for the full posting.
DESCRIPTION_PREVIEW_CHARS = 1000


class NormalizedJob(BaseModel):
    """One posting, normalized across sources. Adapter output, DB input."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source: Source
    source_job_id: str
    company: str
    title: str
    location_raw: str | None = None
    country: Country = "OTHER"
    #: Sub-national region, currently the 2-letter state for US postings. Needed because
    #: "the US" is not a market — CA, NY and MA are.
    subregion: str | None = None
    workplace_type: WorkplaceType = "unknown"
    is_remote: bool = False
    url: str | None = None
    description: str | None = None
    posted_at: str | None = Field(
        default=None, description="Source-provided publish time, UTC ISO 8601."
    )

    # Source-provided compensation (Ashby, Adzuna). Kept separate from the LLM-extracted
    # salary on `Classification`: this is ground truth from the employer, that is inference.
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None

    @field_validator("description")
    @classmethod
    def _truncate_description(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v[:DESCRIPTION_MAX_CHARS]

    @field_validator("source_job_id", mode="before")
    @classmethod
    def _coerce_id(cls, v: object) -> str:
        # Greenhouse returns ints, Lever/Ashby return strings.
        return str(v)


class Classification(BaseModel):
    """LLM classification output. Mirrors the `classifications` table (PLAN §5).

    This is the structured-output schema handed to the model in Phase 2, so field
    descriptions here double as instructions to the classifier.
    """

    category: str = Field(description="Taxonomy slug; must be one of the defined categories.")
    seniority: Seniority = "unknown"
    skills: list[str] = Field(
        default_factory=list,
        description="Normalized lowercase skill/technology names, e.g. ['python','langgraph'].",
    )
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = Field(default=None, description="ISO 4217 code, e.g. 'SGD'.")
    visa_sponsorship: VisaSponsorship = "unknown"
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        description="Self-reported confidence in the assigned category."
    )

    @field_validator("skills")
    @classmethod
    def _normalize_skills(cls, v: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for s in v:
            key = s.strip().lower()
            if key:
                seen.setdefault(key, None)
        return list(seen)

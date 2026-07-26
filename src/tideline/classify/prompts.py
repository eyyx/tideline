"""Classification prompt construction.

The system prompt is the *stable prefix* of every classification request and is marked
with `cache_control`. Two consequences shape everything in this file:

1. It must stay byte-identical across requests. Nothing dynamic (timestamps, job text,
   counts) may appear here — per-job content goes in the user turn.
2. It must exceed Haiku 4.5's minimum cacheable prefix of **4096 tokens**, or caching
   silently does not engage: no error, just `cache_creation_input_tokens: 0` on every
   request forever. A bare taxonomy listing is only ~900 tokens, which is why the worked
   examples below are extensive rather than token-thrifty. They are not padding — they
   cover the boundary cases PLAN §3 calls out, so the same bytes buy both cache
   eligibility and accuracy on the cases the eval measures.

Changing this file changes the cache key and invalidates every cached prefix, so treat
edits as a prompt version bump and re-run the eval (PLAN §7).
"""

from __future__ import annotations

from tideline.taxonomy import BOUNDARY_RULES, CATEGORIES

PROMPT_VERSION = "v1"

_ROLE = """\
You classify technology job postings into a fixed taxonomy. You are used by a job market \
monitoring system that tracks hiring trends across Singapore, the United States, and \
Australia, and that alerts one specific person to roles worth applying to.

Two things follow from that. First, a wrong category is worse than a low-confidence \
right one: the category drives both market statistics and whether a human is notified. \
Second, you must decide from the *job description*, not the title. Titles are marketing; \
descriptions are the job. When title and description disagree, the description wins."""

_TASK = """\
For each posting you receive, produce a structured classification with these fields:

- `category`: exactly one taxonomy slug from the list below.
- `seniority`: one of intern, junior, mid, senior, staff_plus, manager, unknown.
  Judge from scope and required years, not title inflation. "Senior" in a title at a
  small startup often means mid. A role managing people is `manager` even if the title
  says "Lead". Use `unknown` only when the posting genuinely gives no signal.
- `skills`: normalized lowercase technology and tool names explicitly named in the
  posting. Include languages, frameworks, platforms, and named techniques
  (e.g. "python", "pytorch", "langgraph", "mcp", "dbt", "kubernetes", "rag").
  Do NOT include soft skills, degrees, or generic phrases like "machine learning" when a
  specific framework is already listed. Return an empty list if none are named.
- `salary_min`, `salary_max`, `salary_currency`: only when the posting states a salary
  range explicitly. Annualize if given monthly or hourly. Use the ISO 4217 code (USD,
  SGD, AUD). Leave null when not stated — never estimate from market rates.
- `visa_sponsorship`: "yes" if the posting says sponsorship is available, "no" if it
  says citizens/PRs only or explicitly no sponsorship, otherwise "unknown".
- `confidence`: 0.0-1.0, your own confidence in the `category` field specifically.
  Be honest. Postings that are short, vague, or truncated snippets should score low.
  Reserve above 0.9 for postings where the category is unambiguous."""


def _taxonomy_block() -> str:
    lines = ["The categories, with their tier and decision guidance:", ""]
    for category in CATEGORIES:
        lines.append(f"- `{category.slug}` (tier {category.tier}) — {category.label}")
        lines.append(f"    {category.hint}")
    return "\n".join(lines)


def _rules_block() -> str:
    lines = ["Boundary rules. These override any intuition from the title:", ""]
    lines.extend(f"{i}. {rule}" for i, rule in enumerate(BOUNDARY_RULES, start=1))
    return "\n".join(lines)


#: Worked examples. Each pairs a realistic posting summary with the correct label and the
#: reasoning that produces it. Ordered to put the genuinely ambiguous cases last, since
#: those are where classifiers drift.
_EXAMPLES: tuple[tuple[str, str, str], ...] = (
    (
        "Data Scientist, Marketplace — Grab, Singapore. Build demand forecasting models "
        "for driver allocation. Requires Python, SQL, causal inference, and experience "
        "running A/B tests at scale. Partner with product to size opportunities.",
        "data_scientist",
        "Modelling plus experimentation plus product partnership is the classic DS shape. "
        "It is not ml_engineer: nothing here is about serving or infrastructure.",
    ),
    (
        "Machine Learning Engineer — Anthropic, San Francisco. Own the training and "
        "inference stack for production models. Kubernetes, Ray, CUDA, distributed "
        "training. You will optimize throughput and reliability, not design experiments.",
        "ml_engineer",
        "Owns training/serving infrastructure. The explicit disclaimer that it is not "
        "experiment design rules out data_scientist.",
    ),
    (
        "AI Engineer — Notion, New York. Ship LLM-powered features to millions of users. "
        "Prompt engineering, RAG pipelines, evaluation harnesses, working with the "
        "OpenAI and Anthropic APIs. You are building product, not training models.",
        "ai_engineer",
        "Application-layer LLM work against hosted APIs. No model training, so not "
        "ml_engineer. Not agentic_engineer: no agent loop or tool-use focus.",
    ),
    (
        "Agent Engineer — Sierra, San Francisco. Design and ship autonomous agents that "
        "resolve customer issues end to end. You will build tool-calling loops, "
        "multi-step planning, and guardrails. Experience with LangGraph or MCP a plus.",
        "agentic_engineer",
        "Agent loops, tool calling, and multi-step autonomy are the defining signals. "
        "Prefer agentic_engineer over ai_engineer when the JD centres on agents.",
    ),
    (
        "Forward Deployed Engineer — Palantir, Singapore. Embed with government and "
        "commercial customers on site. Build data integrations and custom workflows "
        "directly against the customer's environment. Expect significant travel.",
        "forward_deployed_engineer",
        "Customer-embedded, on-site delivery is the FDE signature.",
    ),
    (
        "Analytics Engineer — Canva, Sydney. Own the dbt models powering company "
        "reporting. Build and test transformation layers in Snowflake, define metrics, "
        "and maintain data quality contracts with upstream teams.",
        "engineering_analyst",
        "Warehouse modelling and transformation ownership. More technical than a "
        "reporting analyst, but not building products, so not software_developer.",
    ),
    (
        "Data Analyst, Growth — Shopee, Singapore. Build dashboards in Tableau, run "
        "weekly business reviews, and answer ad hoc questions from marketing. Strong SQL "
        "and Excel required.",
        "data_analyst",
        "Dashboards, reporting cadence, and stakeholder questions: classic BI analyst.",
    ),
    (
        "Senior Backend Engineer — Stripe, Seattle. Build and scale payment APIs in Go "
        "and Ruby. Own services end to end including on-call.",
        "software_developer",
        "Generic backend engineering. Tier 2 — counted as industry baseline, never alerted.",
    ),
    (
        "Product Manager, ML Platform — Databricks, Remote. Define the roadmap for our "
        "model serving product. Work with engineering to prioritize. No coding required.",
        "other",
        "Product management. The ML subject matter does not make it an engineering role; "
        "classify by what the person does, not the domain they do it in.",
    ),
    (
        "Research Scientist — Google DeepMind, London. Publish at NeurIPS and ICML. "
        "Advance the state of the art in reinforcement learning. PhD and strong "
        "publication record required.",
        "other",
        "Publication-oriented research. Boundary rule 2: Research Scientist is `other`.",
    ),
    (
        "Research Engineer — Cohere, Toronto. Turn research prototypes into scalable "
        "training runs. Implement papers, profile and optimize training throughput, and "
        "maintain the experiment infrastructure. Publications not expected.",
        "ml_engineer",
        "Boundary rule 2 in the other direction: engineering-oriented Research Engineer "
        "is ml_engineer. 'Publications not expected' is the deciding phrase.",
    ),
    (
        "Member of Technical Staff — OpenAI, San Francisco. Work across our post-training "
        "stack. You will run fine-tuning experiments, build evaluation infrastructure, "
        "and improve model quality. Strong PyTorch and distributed systems background.",
        "ml_engineer",
        "Boundary rule 1: decide MTS from the description. Fine-tuning, evals, and "
        "distributed systems put this in ml_engineer, not the generic bucket.",
    ),
    (
        "Member of Technical Staff — Anthropic, Singapore. Partner with enterprise "
        "customers to design and ship Claude-based solutions in their environments. "
        "Significant on-site time with customer engineering teams.",
        "forward_deployed_engineer",
        "Same title, different job. Customer-embedded delivery makes this FDE. This pair "
        "is exactly why title-based classification fails.",
    ),
    (
        "Solutions Engineer — Datadog, Sydney. Support the sales team on technical "
        "discovery calls, run product demos, and answer integration questions during the "
        "evaluation cycle.",
        "other",
        "Boundary rule 4: Solutions Engineer defaults to `other`. This is pre-sales "
        "support, with no embedded delivery work.",
    ),
    (
        "Solutions Architect — Scale AI, Washington DC. Deploy alongside defense "
        "customers on classified programs. You will sit with the customer, build "
        "pipelines against their data, and own delivery through to production.",
        "forward_deployed_engineer",
        "Boundary rule 4's exception: the JD clearly describes customer-embedded "
        "delivery, so it escapes the `other` default.",
    ),
    (
        "Staff Engineer, AI Infrastructure — Figma, San Francisco. Build the platform "
        "teams use to deploy models: feature stores, serving, and observability. You will "
        "not train models yourself.",
        "ml_engineer",
        "ML platform and infrastructure ownership. Precedence rule: ml_engineer beats "
        "software_developer when the platform is specifically for ML.",
    ),
    (
        "Machine Learning Engineer, Agents — Decagon, San Francisco. Build and evaluate "
        "agentic workflows for customer support. You will design tool interfaces, tune "
        "planning loops, and run offline evals of multi-step traces.",
        "agentic_engineer",
        "Precedence rule: agentic_engineer > ai_engineer > ml_engineer. The title says "
        "MLE, but the work is agent design, so the most specific category wins.",
    ),
    (
        "Business Intelligence Analyst — Atlassian, Sydney. Partner with finance on "
        "reporting. Build Looker dashboards, maintain SQL models, and own the weekly "
        "metrics pack.",
        "data_analyst",
        "Reporting-first. Some SQL modelling appears, but the centre of gravity is "
        "dashboards and stakeholder reporting rather than owning the transformation "
        "layer, so data_analyst rather than engineering_analyst.",
    ),
    (
        "Data Engineer — Airwallex, Singapore. Build and operate batch and streaming "
        "pipelines in Spark and Kafka. Own schema design and pipeline reliability for "
        "the data platform.",
        "software_developer",
        "Pipeline and platform engineering with no analytical or modelling remit. It is "
        "not engineering_analyst, which centres on transformation for analysis; this is "
        "infrastructure, so it lands in the tier-2 baseline.",
    ),
    (
        "Applied Scientist, Search Ranking — Sea Group, Singapore. Improve relevance "
        "ranking through offline experimentation and online tests. Publish internally, "
        "own metric definitions, and work with engineers to productionize winners.",
        "data_scientist",
        "Applied Scientist doing experimentation and metric ownership is data_scientist. "
        "The internal-publication mention does not make it Research Scientist: boundary "
        "rule 2 is about publication-*oriented* roles, and productionizing is core here.",
    ),
    (
        "Senior Data Scientist — [truncated snippet] Join our growing analytics team in "
        "Melbourne to drive insight across the customer lifecycle. We are looking for...",
        "data_scientist",
        "An aggregator snippet that cuts off. Title plus 'analytics team' and 'insight' "
        "supports data_scientist, but the evidence is thin — this is a case for "
        "confidence around 0.5, not 0.9.",
    ),
    (
        "LLM Engineer — Harvey, New York. Build retrieval and generation pipelines over "
        "legal documents. Own prompt design, chunking strategy, and eval suites. Some "
        "fine-tuning of open models, but most work is at the application layer.",
        "ai_engineer",
        "Application-layer LLM work is the centre of gravity. The 'some fine-tuning' "
        "aside does not outweigh it — weight the primary responsibility, not every "
        "mentioned task.",
    ),
    (
        "Head of Data — Ninja Van, Singapore. Lead a team of 12 across analytics and "
        "data engineering. Own the data roadmap, hiring, and stakeholder relationships "
        "with the executive team.",
        "other",
        "A leadership role owning an organization rather than doing the work. Seniority "
        "would be `manager`, but the category is `other`: it is not one of the tier-1 "
        "hands-on roles the alerting layer targets.",
    ),
    (
        "Quantitative Researcher — Optiver, Sydney. Develop and backtest trading "
        "strategies. Strong statistics, Python, and C++ required. PhD preferred.",
        "other",
        "Quant finance research is not in the taxonomy. It shares tools with "
        "data_scientist but not the role: resist mapping by skill overlap alone.",
    ),
)


def _examples_block() -> str:
    lines = [
        "Worked examples. Study the reasoning, not just the label — the reasoning is what",
        "generalizes to postings you have not seen.",
        "",
    ]
    for posting, label, reasoning in _EXAMPLES:
        lines.append(f"POSTING: {posting}")
        lines.append(f"CATEGORY: {label}")
        lines.append(f"WHY: {reasoning}")
        lines.append("")
    return "\n".join(lines).rstrip()


_SKILLS_GUIDANCE = """\
Skill extraction rules. Skills feed a demand-trend chart, so consistency across postings \
matters more than completeness on any single one:

- Normalize to the common lowercase form. "PyTorch" and "pytorch" and "Torch" all become
  "pytorch". "Postgres" and "PostgreSQL" both become "postgresql". "K8s" becomes
  "kubernetes". "GCP" becomes "gcp". "Node.js" becomes "node".
- Extract the specific, drop the generic. If a posting says "machine learning frameworks
  such as PyTorch and JAX", return ["pytorch", "jax"], not "machine learning".
- Track emerging agent tooling carefully, since detecting its rise is a core purpose of
  this system: "langgraph", "langchain", "llamaindex", "mcp", "dspy", "autogen",
  "crewai", "pydantic-ai", "vercel ai sdk", "rag", "vector database".
- Cloud providers are skills ("aws", "gcp", "azure"); specific services are too
  ("bedrock", "sagemaker", "vertex ai", "lambda").
- Do NOT extract: soft skills ("communication", "teamwork"), degrees ("bsc", "phd"),
  methodologies as such ("agile", "scrum"), years of experience, or the company's own
  product names unless they are industry-standard tools.
- A posting that names no concrete technology returns an empty list. That is a real and
  common outcome for analyst and management roles — do not invent plausible skills."""

_SENIORITY_GUIDANCE = """\
Seniority calibration. Judge scope, not title:

- `intern` — explicitly an internship, co-op, placement, or new-graduate program.
- `junior` — 0-2 years, "entry level", "graduate", or a JD that emphasizes learning and
  supervision.
- `mid` — 2-5 years, owns features or analyses independently but not team direction.
  This is the correct default when a posting gives years of experience but no other
  seniority signal.
- `senior` — 5+ years, owns systems or workstreams, mentors others, sets local technical
  direction.
- `staff_plus` — Staff, Principal, Distinguished, Senior Staff, or a JD describing
  org-wide technical influence and cross-team architecture ownership.
- `manager` — has direct reports, owns headcount, or runs performance reviews. This wins
  over the individual-contributor levels: an "Engineering Manager" is `manager` even
  when the JD is 50% hands-on coding. A "Tech Lead" with no reports is not `manager`.
- `unknown` — the posting genuinely gives no signal. Prefer `mid` over `unknown` when
  years of experience are stated but the level is not.

Title inflation is real and regional. A "Senior Engineer" at a 20-person startup
requiring 3 years is `mid`. A "Software Engineer II" at a large firm requiring 6 years
is `senior`. Read the requirements section, not the header."""

_CLOSING = """\
Classify the posting in the next message. Return only the structured fields. Do not \
explain your reasoning in the output — the reasoning above is for your benefit, not \
something to reproduce.

Truncated snippets are expected. Some postings in this system come from an aggregator \
that supplies only the first few hundred characters, so you will regularly see \
descriptions that cut off mid-sentence. Classify from what is present, lean on the title \
more heavily than you otherwise would, and lower `confidence` to reflect the thin \
evidence — typically 0.4-0.6 for a snippet whose category is plausible but unconfirmed. \
Do not refuse, do not ask for more information, and do not invent details that are not \
in the text."""


def build_system_prompt() -> str:
    """The cached prefix. Deterministic — same bytes on every call, forever."""
    return "\n\n".join(
        (
            _ROLE,
            _TASK,
            _taxonomy_block(),
            _rules_block(),
            _SKILLS_GUIDANCE,
            _SENIORITY_GUIDANCE,
            _examples_block(),
            _CLOSING,
        )
    )


SYSTEM_PROMPT = build_system_prompt()


def build_user_content(
    *,
    title: str,
    company: str,
    location: str | None,
    description: str | None,
    max_description_chars: int = 4000,
) -> str:
    """The per-job turn. Everything volatile lives here, after the cache breakpoint."""
    parts = [
        f"TITLE: {title}",
        f"COMPANY: {company}",
        f"LOCATION: {location or 'not stated'}",
        "DESCRIPTION:",
        (description or "(no description provided)")[:max_description_chars],
    ]
    return "\n".join(parts)

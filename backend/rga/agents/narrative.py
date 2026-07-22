"""G1 narrative agent (hybrid). The requirement-bearing SRS sections are rendered
deterministically from the repository; the *prose* sections (Purpose, Product Scope,
Product Perspective, …) are drafted here by the LLM from the approved requirements and
project context. Anything the model leaves blank becomes `[TBD - Design/BA input]` in the
SRS — we never fabricate prose, and every draft is clearly a draft for human review.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

from ..generate.common import approved_sorted
from ..llm.base import LLMProvider
from ..logging_setup import get_logger
from ..models import Requirement

log = get_logger("rga.narrative")

# The narrative writes ~13 prose sections PLUS one stimulus/response flow per feature in a
# single JSON object — give it ample output room so the response is never truncated (a
# truncated JSON string is unrepairable; if it happens we still fall back gracefully, but we
# want the richer LLM prose to survive). Cap the INPUT digest so a large approved set doesn't
# bloat the prompt or push an over-long answer.
NARRATIVE_MAX_TOKENS = 12000
MAX_CONTEXT_REQUIREMENTS = 80

NARRATIVE_SYSTEM = (
    "You are a senior business analyst drafting the prose sections of an IEEE-830 Software "
    "Requirements Specification. Write ONLY from the approved requirements and context "
    "provided — do not invent capabilities. Be concise and factual. If you cannot support a "
    "section from the given material, return an empty string for it (it will be marked TBD). "
    "For `feature_flows`, write one entry per System Feature named in the context, using the "
    "EXACT feature name. Each entry's `sequences` must be a list of 2-4 DISCRETE interaction "
    "sequences — NOT one packed paragraph. Each sequence is ONE actor, ONE flow: a `stimulus` "
    "(a single concrete action the user or an integrating system takes) and the system's "
    "`response` (what the system does and returns). Cover the primary success flow first, then "
    "the main alternate/error flow(s), each grounded in that feature's requirements. Omit a "
    "feature only if its requirements give you nothing to describe."
)

# LLM field  ->  SRS section number it fills.
FIELD_TO_SECTION: dict[str, str] = {
    "purpose": "1.1",
    "intended_audience": "1.3",
    "product_scope": "1.4",
    "product_perspective": "2.1",
    "product_functions": "2.2",
    "user_classes": "2.3",
    "operating_environment": "2.4",
    "user_interfaces": "3.1",
    "visual_theme": "3.1.1",
    "layout_standards": "3.1.5",
    "hardware_interfaces": "3.2",
    "software_interfaces": "3.3",
    "communications_interfaces": "3.4",
}


class StimulusResponse(BaseModel):
    """One DISCRETE §4.x.2 interaction: a single stimulus and the system's response."""
    stimulus: str = ""
    response: str = ""


class FeatureFlow(BaseModel):
    """The §4.x.2 Stimulus/Response Sequences for one feature, keyed by the exact feature name.
    `sequences` is a list of DISCRETE stimulus→response pairs (the QuickBite shape). `flow` is a
    legacy single-string fallback kept only so an older single-paragraph response still renders."""
    feature: str = ""
    sequences: list[StimulusResponse] = Field(default_factory=list)
    flow: str = ""  # legacy fallback


def render_feature_flow(sequences: list[StimulusResponse]) -> str:
    """Render discrete stimulus/response sequences as a numbered §4.x.2 block — each sequence its
    own item with an explicit **Stimulus** and **Response**, matching the reference SRS shape (not
    one packed paragraph)."""
    lines: list[str] = []
    n = 0
    for sq in sequences:
        stim = (sq.stimulus or "").strip()
        resp = (sq.response or "").strip()
        if not stim and not resp:
            continue
        n += 1
        lines.append(f"{n}. **Stimulus —** {stim}")
        lines.append(f"   **Response —** {resp}")
    return "\n".join(lines)


class NarrativeSections(BaseModel):
    purpose: str = ""
    intended_audience: str = ""
    product_scope: str = ""
    product_perspective: str = ""
    product_functions: str = ""
    user_classes: str = ""
    operating_environment: str = ""
    user_interfaces: str = ""
    visual_theme: str = ""       # 3.1.1 Overall Visual Theme
    layout_standards: str = ""   # 3.1.5 Layout and Interaction Standards
    hardware_interfaces: str = ""
    software_interfaces: str = ""
    communications_interfaces: str = ""
    feature_flows: list[FeatureFlow] = Field(default_factory=list)  # §4.x.2 per feature


def _context(project_name: str, requirements: list[Requirement]) -> str:
    """A bounded, high-level digest of the approved requirements — enough for the model to
    write summary prose, without dumping hundreds of statements into the prompt."""
    from ..generate.srs_template import canonical_feature

    appr = approved_sorted(requirements)
    by_type = Counter(r.rtype.value for r in appr)
    # Use CANONICAL feature names so the LLM's per-feature stimulus/response flows key by the SAME
    # names §4 groups on (Stage 3a consolidation) — otherwise the flows never match and §4.x.2 falls
    # back to the generic default.
    features = sorted({canonical_feature(r.feature) for r in appr
                       if r.rtype.value == "functional" and r.feature})

    lines = [
        f"Project: {project_name}",
        f"Approved requirements: {len(appr)} total ("
        + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) + ").",
    ]
    if features:
        lines.append("System features: " + ", ".join(features[:25]))
    lines += ["", f"Representative requirements (up to {MAX_CONTEXT_REQUIREMENTS}):"]
    for r in appr[:MAX_CONTEXT_REQUIREMENTS]:
        feat = f" [{canonical_feature(r.feature)}]" if r.feature else ""
        lines.append(f"- ({r.rtype.value}{feat}) {r.statement}")
    if len(appr) > MAX_CONTEXT_REQUIREMENTS:
        lines.append(f"... and {len(appr) - MAX_CONTEXT_REQUIREMENTS} more (summarise; do not enumerate).")
    return "\n".join(lines)


def draft_narrative(
    provider: LLMProvider,
    project_name: str,
    requirements: list[Requirement],
    *,
    run_llm: bool = True,
) -> dict[str, str]:
    """Return {section_number: prose} for the sections the model could support. Sections
    not returned (or empty) are simply omitted, and the SRS assembler renders them as TBD."""
    if not run_llm or provider is None:
        return {}
    user = (
        _context(project_name, requirements)
        + "\n\nDraft the SRS prose sections from the above. Keep each section brief (a short "
        "paragraph). Leave a field empty if the material does not support it. Also provide "
        "`feature_flows`: one entry per System Feature listed above (exact feature name), whose "
        "`sequences` is a list of 2-4 DISCRETE stimulus→response pairs (primary flow first, then "
        "the main alternate/error flow) — each a single actor action and the system's response, "
        "not one combined paragraph."
    )
    try:
        result = provider.structured(
            NARRATIVE_SYSTEM, user, NarrativeSections, max_tokens=NARRATIVE_MAX_TOKENS
        )
    except Exception as exc:  # best-effort: never block SRS/RTM generation on the prose step
        log.warning("narrative drafting failed (%s); prose sections will render as TBD", exc)
        return {}
    out: dict[str, str] = {}
    for field, section in FIELD_TO_SECTION.items():
        text = (getattr(result, field, "") or "").strip()
        if text:
            out[section] = text
    for ff in getattr(result, "feature_flows", []) or []:  # §4.x.2, keyed "feature_flow::<name>"
        name = (ff.feature or "").strip()
        if not name:
            continue
        rendered = render_feature_flow(ff.sequences or [])   # discrete sequences (preferred shape)
        if not rendered:
            rendered = (ff.flow or "").strip()               # legacy single-string fallback
        if rendered:
            out[f"feature_flow::{name}"] = rendered
    return out

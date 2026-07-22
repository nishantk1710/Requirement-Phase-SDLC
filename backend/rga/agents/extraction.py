"""A1 — Extraction agent. Pulls atomic requirements from a chunk, each with the exact
verbatim quote(s) that support it. The verbatim guarantee is ENFORCED in code (the
pipeline drops any quote that is not actually in the chunk) — the prompt only asks for it.
"""

from __future__ import annotations

from ..llm.base import LLMProvider
from ..models import Chunk
from .schemas import ExtractedRequirement, ExtractionResult

EXTRACTION_SYSTEM = (
    "You are a meticulous requirements analyst. You extract requirements ONLY from the "
    "source text you are given. You never invent requirements. For every requirement you "
    "output, you copy the exact supporting sentence(s) verbatim from the source."
)


def _user_prompt(chunk: Chunk, hints: list[str] | None) -> str:
    hint_block = ""
    if hints:
        hint_block = (
            "\n\nA reviewer suspects the following may have been missed — include each "
            "ONLY if the source text supports it:\n- " + "\n- ".join(hints)
        )
    return (
        f"Document type: {chunk.source_type}. Location: {chunk.location}.\n"
        f'SOURCE TEXT:\n"""\n{chunk.text}\n"""\n\n'
        "Extract every distinct, atomic requirement that is stated or clearly implied in "
        "the SOURCE TEXT. For each requirement provide:\n"
        "  - statement: a single normalized requirement (one obligation).\n"
        "  - rtype: classify PRECISELY —\n"
        "      * functional = something the system DOES (a behaviour or capability). A capability "
        "is functional even when it concerns the cart, filters, search, or admin views.\n"
        "      * non_functional = a QUALITY attribute (performance, security, usability, reliability, "
        "maintainability, portability, compatibility, safety) — NOT a feature or behaviour.\n"
        "      * business = a business rule/policy (e.g. tax, returns window, eligibility).\n"
        "      * constraint = a design/technology/regulatory limitation.\n"
        "      * assumption = a dependency or assumption.\n"
        "  - feature: for FUNCTIONAL requirements only, the short capability group it belongs to "
        "(reuse a consistent name across related requirements, e.g. 'Search & Browse', 'Product "
        "Detail', 'Cart & Checkout', 'Payments', 'Orders & Fulfilment', 'Returns', 'Reviews', "
        "'Account', 'Admin', 'Notifications'); omit for non-functional/business/constraint/assumption.\n"
        "  - nfr_category: for NON_FUNCTIONAL requirements only, exactly one of: performance | "
        "security | usability | reliability | maintainability | portability | compatibility | safety.\n"
        "  - quotes: one or more spans copied VERBATIM from the SOURCE TEXT that support it "
        "(you may include several spans if it is spread across sentences).\n"
        "  - inferred: true only if the requirement is implied rather than stated outright.\n"
        "  - rationale: a brief why; confidence: 0.0-1.0.\n"
        "Do NOT output any requirement that the SOURCE TEXT does not support. Output exactly "
        "ONE requirement per distinct obligation: do not restate the same obligation in "
        "different words, and do not split a single obligation into overlapping fragments.\n"
        "Extract ONLY genuine, testable obligations of the product, system, or business/process. "
        "Do NOT extract: statements about the document itself (e.g. 'this document…', section "
        "titles/headings), preambles, disclaimers, scope notes, meeting pleasantries, or vague "
        "aspirations that impose no checkable obligation. Also do NOT extract project-management, "
        "staffing, or ownership notes (who sponsors the project, who leads engineering, who owns an "
        "inbox, who must be informed/consulted, deadlines, or team assignments) — these are not "
        "product requirements. Also do NOT extract business goals, success metrics, KPIs, or pain "
        "points (e.g. 'increase repeat purchases', 'reduce checkout drop-off', 'stop losing customers') "
        "— these are objectives, not requirements.\n"
        "RESPECT SCOPE AND NEGATION: if the SOURCE TEXT or its section/heading (see Location above) "
        "marks something as out of scope, excluded, deferred, 'not for launch', 'won't', 'broadly out', "
        "or 'later phase', do NOT state it as a positive requirement. If the exclusion matters, record it "
        "as a constraint phrased AS an exclusion (e.g. 'Multi-currency is out of scope for this release'). "
        "Never invert an exclusion into a 'must support'.\n"
        "HANDLE UNDECIDED ITEMS: if the source marks something as undecided, optional, a 'maybe', a "
        "'nice to have', or still open, set inferred=true and phrase it tentatively — do NOT assert it as a "
        "firm 'shall'.\n"
        "PRESERVE THE WHOLE REQUIREMENT: keep every stated option, alternative, and qualifier (e.g. "
        "'percentage OR flat amount', 'email AND SMS') — do not narrow or drop alternatives.\n"
        "DO NOT treat internal document/section references or ticket keys (e.g. 'A3', 'B2', 'C1', "
        "'HRZN-12') as product entities, features, or products — they are pointers to other requirements.\n"
        "If a sentence does not place a concrete, verifiable obligation on the system or business, skip it." + hint_block
    )


def extract_from_chunk(
    provider: LLMProvider, chunk: Chunk, hints: list[str] | None = None
) -> list[ExtractedRequirement]:
    result = provider.structured(
        EXTRACTION_SYSTEM, _user_prompt(chunk, hints), ExtractionResult
    )
    return result.requirements

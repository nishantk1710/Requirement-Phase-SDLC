"""Structured-output schemas for the P3 agents. Every agent returns one of these via
`LLMProvider.structured()`, so outputs are always validated (never raw text)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedRequirement(BaseModel):
    statement: str  # normalized, atomic requirement
    rtype: str = "functional"  # functional|non_functional|business|constraint|assumption
    feature: str | None = None  # functional only: capability group (SRS §4 grouping)
    nfr_category: str | None = None  # non_functional only: performance|security|usability|...
    quotes: list[str] = Field(default_factory=list)  # VERBATIM spans from the chunk (multi-span ok)
    inferred: bool = False  # true = implied, not stated outright
    rationale: str = ""
    confidence: float = 0.5


class ExtractionResult(BaseModel):
    requirements: list[ExtractedRequirement] = Field(default_factory=list)


class CriticVerdict(BaseModel):
    grounded: bool  # does the statement follow ONLY from the cited quotes / source?
    invented: bool = False  # does it assert something the source does not support?
    possibly_missed: list[str] = Field(default_factory=list)  # requirements the extractor may have missed
    reason: str = ""

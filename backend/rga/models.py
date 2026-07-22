"""Domain model — the spine of the system.

`Requirement` is the central record. The SRS and RTM are later generated as *views*
of these objects, so they can never drift from what was approved. Every requirement
must carry at least one `SourceRef` with an exact `raw_quote` (anti-hallucination).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RType(str, Enum):
    functional = "functional"
    non_functional = "non_functional"
    business = "business"
    constraint = "constraint"
    assumption = "assumption"


class Status(str, Enum):
    candidate = "candidate"
    needs_review = "needs_review"
    approved = "approved"
    rejected = "rejected"


class Priority(str, Enum):
    must = "must"
    should = "should"
    could = "could"
    wont = "wont"


class SourceRef(BaseModel):
    """Exact provenance of a requirement — the proof it was not invented.

    `raw_quote` is the SOURCE document's own bytes for [start:end] (document-level
    character offsets), so the span is exactly locatable/highlightable in the original.
    `start`/`end` are optional only for hand-authored refs (e.g. gold/tests)."""

    doc_id: str
    source_type: str  # brd | transcript | email | form | jira | legacy
    location: str  # page/section/line/ticket-key
    raw_quote: str  # verbatim source text (may span multiple sentences)
    start: int | None = None  # document-level char offset (inclusive)
    end: int | None = None  # document-level char offset (exclusive)


class Quality(BaseModel):
    ambiguity_flags: list[str] = Field(default_factory=list)
    ambiguity_explanation: str | None = None
    suggested_rewrite: str | None = None  # A3's EARS-style rewrite of a weak requirement
    testable: bool | None = None
    completeness_notes: str | None = None
    score: float | None = None


class Requirement(BaseModel):
    id: str
    project_id: str
    statement: str
    rtype: RType = RType.functional
    nfr_category: str | None = None  # ISO/IEC 25010 attribute if NFR
    feature: str | None = None  # System-Feature grouping for SRS section 4 (functional reqs)
    priority: Priority | None = None  # MoSCoW — proposed in Wave 2; human decides
    inferred: bool = False  # True -> routed to human, never auto-accepted
    source_refs: list[SourceRef] = Field(default_factory=list)
    quality: Quality = Field(default_factory=Quality)
    duplicate_of: list[str] = Field(default_factory=list)  # Wave 2
    conflicts_with: list[str] = Field(default_factory=list)  # Wave 2
    status: Status = Status.candidate
    rationale: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)  # triage/auto-approve assume a [0,1] score
    provenance: dict = Field(default_factory=dict)  # {agent, model, provider, ts, ...}
    created_at: datetime = Field(default_factory=_utcnow)


class ReviewAction(str, Enum):
    accept = "accept"
    edit = "edit"
    reject = "reject"


class ReviewDecision(BaseModel):
    """One human decision in the review gate — powers audit + the time-saved metric."""

    requirement_id: str
    action: ReviewAction
    before: dict | None = None
    after: dict | None = None
    actor: str = "reviewer"
    ts: datetime = Field(default_factory=_utcnow)


class AgentRun(BaseModel):
    """One agent execution — audit trail."""

    project_id: str
    agent: str
    model: str | None = None
    provider: str | None = None
    status: str = "success"
    input: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)
    ts: datetime = Field(default_factory=_utcnow)


class Project(BaseModel):
    id: str
    name: str
    status: str = "created"
    created_at: datetime = Field(default_factory=_utcnow)


class Chunk(BaseModel):
    """A structure-aware slice of a source document, with exact character offsets so
    it stays traceable back to the original text."""

    doc_id: str
    project_id: str | None = None
    source_type: str
    index: int  # position of this chunk within its document
    location: str  # human label (heading trail / speaker / row id / ticket key)
    text: str  # verbatim slice of the source document
    start: int  # character offset (inclusive) into the raw document
    end: int  # character offset (exclusive) into the raw document


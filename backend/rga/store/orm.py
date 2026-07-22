"""SQLAlchemy ORM tables. Kept separate from the Pydantic domain models; the
repository maps between the two."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime)


class RequirementRow(Base):
    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id"), index=True
    )
    statement: Mapped[str] = mapped_column(Text)
    rtype: Mapped[str] = mapped_column(String)
    nfr_category: Mapped[str | None] = mapped_column(String, nullable=True)
    feature: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[str | None] = mapped_column(String, nullable=True)
    inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="candidate")
    rationale: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    quality: Mapped[dict] = mapped_column(JSON, default=dict)
    duplicate_of: Mapped[list] = mapped_column(JSON, default=list)
    conflicts_with: Mapped[list] = mapped_column(JSON, default=list)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    source_refs: Mapped[list["SourceRefRow"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class SourceRefRow(Base):
    __tablename__ = "source_refs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[str] = mapped_column(
        String, ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    doc_id: Mapped[str] = mapped_column(String)
    source_type: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String)
    raw_quote: Mapped[str] = mapped_column(Text)
    start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ReviewDecisionRow(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String, default="reviewer")
    ts: Mapped[datetime] = mapped_column(DateTime)


class DecisionResolutionRow(Base):
    """A persisted resolution of a review DECISION (conflict / scope call / gap), so resolutions
    survive a reload and the decisions list can report resolved-vs-open. One row per (project,
    decision_id); re-resolving replaces it."""
    __tablename__ = "decision_resolutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String, index=True)
    decision_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    recommended: Mapped[str] = mapped_column(String, default="")
    action: Mapped[str] = mapped_column(String)       # what was applied (accept/reject/keep-a/acknowledged…)
    actor: Mapped[str] = mapped_column(String, default="reviewer")
    ts: Mapped[datetime] = mapped_column(DateTime)


class AgentRunRow(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String, index=True)
    agent: Mapped[str] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="success")
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime)


class ChunkRow(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    doc_id: Mapped[str] = mapped_column(String, index=True)
    source_type: Mapped[str] = mapped_column(String)
    idx: Mapped[int] = mapped_column(Integer)
    location: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)

"""Repository — the only place that maps domain (Pydantic) <-> ORM rows.

Agents and the pipeline use domain models; persistence details stay here.
`save_requirement` is an idempotent upsert (delete-then-insert incl. child rows).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from ..models import (
    AgentRun,
    Chunk,
    Priority,
    Project,
    Quality,
    Requirement,
    ReviewAction,
    ReviewDecision,
    RType,
    SourceRef,
    Status,
)
from .db import Database
from .orm import (
    AgentRunRow,
    ChunkRow,
    DecisionResolutionRow,
    ProjectRow,
    RequirementRow,
    ReviewDecisionRow,
    SourceRefRow,
)


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- projects ------------------------------------------------------------
    async def save_project(self, p: Project) -> None:
        async with self.db.session() as s:
            async with s.begin():
                await s.merge(
                    ProjectRow(
                        id=p.id, name=p.name, status=p.status, created_at=p.created_at
                    )
                )

    async def get_project(self, project_id: str) -> Project | None:
        async with self.db.session() as s:
            row = await s.get(ProjectRow, project_id)
            if row is None:
                return None
            return Project(
                id=row.id, name=row.name, status=row.status, created_at=row.created_at
            )

    # --- requirements --------------------------------------------------------
    async def save_requirement(self, r: Requirement) -> None:
        async with self.db.session() as s:
            async with s.begin():
                existing = await s.get(RequirementRow, r.id)
                if existing is not None:
                    # Structural guard: ids are project-scoped (see agents.pipeline._req_id), so a
                    # collision across projects should be impossible. If one ever occurs, fail LOUD
                    # rather than silently reparenting another project's requirement.
                    if existing.project_id != r.project_id:
                        raise ValueError(
                            f"requirement id {r.id!r} already belongs to project "
                            f"{existing.project_id!r}; refusing to reparent it to {r.project_id!r}"
                        )
                    await s.delete(existing)
                    await s.flush()
                s.add(self._to_row(r))

    async def reset_project_requirements(self, project_id: str) -> int:
        """Clear a project's requirements (+ their source-refs via FK cascade) and the review
        decisions attached to them, so a fresh run never mixes with a prior run's rows (or leaves a
        renamed-statement orphan still `approved`). Keeps the project row, its chunks, and the
        agent-run history. Returns the number of requirements removed."""
        async with self.db.session() as s:
            async with s.begin():
                rids = [
                    r for (r,) in (
                        await s.execute(
                            select(RequirementRow.id).where(RequirementRow.project_id == project_id)
                        )
                    ).all()
                ]
                if rids:
                    await s.execute(
                        delete(ReviewDecisionRow).where(ReviewDecisionRow.requirement_id.in_(rids))
                    )
                await s.execute(delete(RequirementRow).where(RequirementRow.project_id == project_id))
                # a fresh run recomputes decisions -> prior resolutions are stale, clear them
                await s.execute(delete(DecisionResolutionRow).where(DecisionResolutionRow.project_id == project_id))
        return len(rids)

    async def get_requirement(self, requirement_id: str) -> Requirement | None:
        async with self.db.session() as s:
            res = await s.execute(
                select(RequirementRow)
                .where(RequirementRow.id == requirement_id)
                .options(selectinload(RequirementRow.source_refs))
            )
            row = res.scalar_one_or_none()
            return self._from_row(row) if row is not None else None

    async def list_requirements(self, project_id: str) -> list[Requirement]:
        async with self.db.session() as s:
            res = await s.execute(
                select(RequirementRow)
                .where(RequirementRow.project_id == project_id)
                .options(selectinload(RequirementRow.source_refs))
                .order_by(RequirementRow.id)
            )
            return [self._from_row(row) for row in res.scalars().all()]

    async def delete_project(self, project_id: str) -> int:
        """Remove a project and ALL its data (requirements + their source-refs via FK
        cascade, chunks, agent-runs, review-decisions, and the project row). Returns the
        number of requirements removed."""
        async with self.db.session() as s:
            async with s.begin():
                rids = [
                    r for (r,) in (
                        await s.execute(
                            select(RequirementRow.id).where(RequirementRow.project_id == project_id)
                        )
                    ).all()
                ]
                await s.execute(delete(RequirementRow).where(RequirementRow.project_id == project_id))
                await s.execute(delete(ChunkRow).where(ChunkRow.project_id == project_id))
                await s.execute(delete(AgentRunRow).where(AgentRunRow.project_id == project_id))
                await s.execute(delete(DecisionResolutionRow).where(DecisionResolutionRow.project_id == project_id))
                if rids:
                    await s.execute(delete(ReviewDecisionRow).where(ReviewDecisionRow.requirement_id.in_(rids)))
                await s.execute(delete(ProjectRow).where(ProjectRow.id == project_id))
        return len(rids)

    # --- review decisions ----------------------------------------------------
    async def log_review_decision(self, d: ReviewDecision) -> None:
        async with self.db.session() as s:
            async with s.begin():
                s.add(
                    ReviewDecisionRow(
                        requirement_id=d.requirement_id,
                        action=d.action.value,
                        before=d.before,
                        after=d.after,
                        actor=d.actor,
                        ts=d.ts,
                    )
                )

    async def list_review_decisions(self, requirement_id: str) -> list[ReviewDecision]:
        async with self.db.session() as s:
            res = await s.execute(
                select(ReviewDecisionRow)
                .where(ReviewDecisionRow.requirement_id == requirement_id)
                .order_by(ReviewDecisionRow.id)
            )
            return [
                ReviewDecision(
                    requirement_id=x.requirement_id,
                    action=ReviewAction(x.action),
                    before=x.before,
                    after=x.after,
                    actor=x.actor,
                    ts=x.ts,
                )
                for x in res.scalars().all()
            ]

    # --- decision resolutions ------------------------------------------------
    async def save_decision_resolution(
        self, project_id: str, decision_id: str, kind: str, recommended: str, action: str,
        actor: str = "reviewer",
    ) -> None:
        """Persist (idempotently) the resolution of one review decision, so it survives a reload and
        no longer shows as open. Re-resolving the same decision replaces the prior row."""
        async with self.db.session() as s:
            async with s.begin():
                await s.execute(
                    delete(DecisionResolutionRow).where(
                        DecisionResolutionRow.project_id == project_id,
                        DecisionResolutionRow.decision_id == decision_id,
                    )
                )
                s.add(DecisionResolutionRow(
                    project_id=project_id, decision_id=decision_id, kind=kind,
                    recommended=recommended, action=action, actor=actor,
                    ts=datetime.now(timezone.utc),
                ))

    async def resolved_decision_ids(self, project_id: str) -> set[str]:
        """The set of decision ids already resolved for a project."""
        async with self.db.session() as s:
            res = await s.execute(
                select(DecisionResolutionRow.decision_id).where(
                    DecisionResolutionRow.project_id == project_id
                )
            )
            return {d for (d,) in res.all()}

    async def list_decision_resolutions(self, project_id: str) -> list[dict]:
        """The full resolution log for a project (audit)."""
        async with self.db.session() as s:
            res = await s.execute(
                select(DecisionResolutionRow)
                .where(DecisionResolutionRow.project_id == project_id)
                .order_by(DecisionResolutionRow.id)
            )
            return [
                {"decision_id": r.decision_id, "kind": r.kind, "recommended": r.recommended,
                 "action": r.action, "actor": r.actor, "ts": r.ts.isoformat() if r.ts else None}
                for r in res.scalars().all()
            ]

    # --- agent runs ----------------------------------------------------------
    async def log_agent_run(self, run: AgentRun) -> None:
        async with self.db.session() as s:
            async with s.begin():
                s.add(
                    AgentRunRow(
                        project_id=run.project_id,
                        agent=run.agent,
                        model=run.model,
                        provider=run.provider,
                        status=run.status,
                        input=run.input,
                        output=run.output,
                        ts=run.ts,
                    )
                )

    async def count_agent_runs(self, project_id: str) -> int:
        async with self.db.session() as s:
            res = await s.execute(
                select(func.count()).select_from(AgentRunRow).where(AgentRunRow.project_id == project_id)
            )
            return int(res.scalar_one())

    async def latest_agent_run_output(self, project_id: str) -> dict | None:
        """The `output` of the most recent agent run for a project (e.g. to recover the
        extraction's open-questions when generating the SRS)."""
        async with self.db.session() as s:
            res = await s.execute(
                select(AgentRunRow)
                .where(AgentRunRow.project_id == project_id)
                .order_by(AgentRunRow.id.desc())
                .limit(1)
            )
            row = res.scalars().first()
            return row.output if row is not None else None

    # --- chunks --------------------------------------------------------------
    async def save_chunks(self, chunks: list[Chunk]) -> None:
        """Replace the affected (project, doc) chunk sets, then insert the given ones.

        The delete is scoped by BOTH project_id and doc_id — two projects that share a doc_id
        (both shipped corpora use "brd") must not delete each other's chunks."""
        if not chunks:
            return
        pairs = {(c.project_id, c.doc_id) for c in chunks}
        async with self.db.session() as s:
            async with s.begin():
                for project_id, doc_id in pairs:
                    await s.execute(
                        delete(ChunkRow).where(
                            ChunkRow.project_id == project_id, ChunkRow.doc_id == doc_id
                        )
                    )
                await s.flush()
                for c in chunks:
                    s.add(
                        ChunkRow(
                            project_id=c.project_id,
                            doc_id=c.doc_id,
                            source_type=c.source_type,
                            idx=c.index,
                            location=c.location,
                            text=c.text,
                            start=c.start,
                            end=c.end,
                        )
                    )

    async def list_project_chunks(self, project_id: str) -> list[Chunk]:
        """All chunks for a project, ordered by (doc_id, index) — used by the orchestrator
        to resume extraction from persisted chunks rather than re-ingesting."""
        async with self.db.session() as s:
            res = await s.execute(
                select(ChunkRow)
                .where(ChunkRow.project_id == project_id)
                .order_by(ChunkRow.doc_id, ChunkRow.idx)
            )
            return [
                Chunk(
                    doc_id=r.doc_id, project_id=r.project_id, source_type=r.source_type,
                    index=r.idx, location=r.location, text=r.text, start=r.start, end=r.end,
                )
                for r in res.scalars().all()
            ]

    async def list_chunks(self, doc_id: str) -> list[Chunk]:
        async with self.db.session() as s:
            res = await s.execute(
                select(ChunkRow)
                .where(ChunkRow.doc_id == doc_id)
                .order_by(ChunkRow.idx)
            )
            return [
                Chunk(
                    doc_id=r.doc_id,
                    project_id=r.project_id,
                    source_type=r.source_type,
                    index=r.idx,
                    location=r.location,
                    text=r.text,
                    start=r.start,
                    end=r.end,
                )
                for r in res.scalars().all()
            ]

    # --- mapping -------------------------------------------------------------
    @staticmethod
    def _to_row(r: Requirement) -> RequirementRow:
        return RequirementRow(
            id=r.id,
            project_id=r.project_id,
            statement=r.statement,
            rtype=r.rtype.value,
            nfr_category=r.nfr_category,
            feature=r.feature,
            priority=(r.priority.value if r.priority else None),
            inferred=r.inferred,
            status=r.status.value,
            rationale=r.rationale,
            confidence=r.confidence,
            quality=r.quality.model_dump(),
            duplicate_of=list(r.duplicate_of),
            conflicts_with=list(r.conflicts_with),
            provenance=r.provenance,
            created_at=r.created_at,
            source_refs=[
                SourceRefRow(
                    doc_id=s.doc_id,
                    source_type=s.source_type,
                    location=s.location,
                    raw_quote=s.raw_quote,
                    start=s.start,
                    end=s.end,
                )
                for s in r.source_refs
            ],
        )

    @staticmethod
    def _from_row(row: RequirementRow) -> Requirement:
        return Requirement(
            id=row.id,
            project_id=row.project_id,
            statement=row.statement,
            rtype=RType(row.rtype),
            nfr_category=row.nfr_category,
            feature=row.feature,
            priority=(Priority(row.priority) if row.priority else None),
            inferred=row.inferred,
            status=Status(row.status),
            rationale=row.rationale,
            confidence=row.confidence,
            quality=Quality(**(row.quality or {})),
            duplicate_of=list(row.duplicate_of or []),
            conflicts_with=list(row.conflicts_with or []),
            provenance=row.provenance or {},
            created_at=row.created_at,
            source_refs=[
                SourceRef(
                    doc_id=s.doc_id,
                    source_type=s.source_type,
                    location=s.location,
                    raw_quote=s.raw_quote,
                    start=s.start,
                    end=s.end,
                )
                for s in row.source_refs
            ],
        )

"""FastAPI app for the RGA UI (P6+).

This is the single control surface for the whole pipeline — no terminal needed:
  GET  /api/health
  GET  /api/config                              -> provider/model in use
  GET  /api/corpora                             -> available document sets (datasets + uploads)
  POST /api/projects/{pid}/upload               -> upload documents (multipart) -> a corpus
  POST /api/projects/{pid}/run                  -> run ingest+extract+analyze in the background
  GET  /api/projects/{pid}/status               -> live run status (for progress)
  GET  /api/projects/{pid}/requirements[?include=all|pending|approved]
  GET  /api/projects/{pid}/gate                 -> ready_for_generation + status counts
  POST /api/requirements/{rid}/review           -> accept | edit | reject (logs the decision)
  GET  /api/requirements/{rid}                  -> one requirement + its decision log
  POST /api/projects/{pid}/generate             -> 409 unless the gate is open; else builds SRS+RTM
  GET  /api/projects/{pid}/artifacts            -> list generated files
  GET  /api/projects/{pid}/artifacts/{name}     -> a generated file's content (view/download)

The HARD GATE (review.gate) still applies: generation is refused until every requirement is
triaged and at least one approved. A built frontend (frontend/dist) is served at /.
"""

from __future__ import annotations

import asyncio
import datetime
import io
import json
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agents.owner import owner_of
from ..agents.triage import needs_attention, triage, triage_summary
from ..logging_setup import get_logger
from ..models import AgentRun, Project, Requirement, ReviewAction, Status
from ..review.gate import PENDING, approved_only, counts, ready_for_generation
from ..review.service import ReviewError, apply_decision
from ..util.paths import safe_dir_component

log = get_logger("rga.api")

# Upload extension -> source_type the chunker understands.
_EXT_TYPE = {".md": "brd", ".txt": "brd", ".csv": "form", ".json": "jira",
             ".eml": "email", ".pdf": "brd", ".docx": "brd"}


class ReviewBody(BaseModel):
    action: ReviewAction
    edits: dict | None = None
    actor: str = "reviewer"


class RunBody(BaseModel):
    corpus: str = "datasets/elams"
    run_critic: bool = True   # A0 verifies grounding AND filters non-requirements (precision)
    max_passes: int = 2       # a 2nd pass re-checks "possibly missed" for recall; stops early if converged
    max_workers: int = 8      # extract chunks in parallel; the network calls are the cost
    adversarial_verify: bool = True  # independent second-opinion review of the high-risk subset (precision)
    consolidate: bool = True  # converge to a canonical set (LLM merge-biased) + demote pointer reqs
    auto_approve: bool = True        # auto-approve clean, confident, ROUTINE reqs (kept out of the human queue)
    auto_approve_bar: float = 0.9    # confidence bar for auto-approval


class AutoAcceptBody(BaseModel):
    min_confidence: float = 0.9
    actor: str = "auto-rule"


class BulkBody(BaseModel):
    ids: list[str]
    action: ReviewAction
    actor: str = "reviewer"


class TechStackSelectBody(BaseModel):
    aspect: str                       # the aspect key (frontend, backend, database, …)
    candidate: str                    # the chosen candidate's `name`, OR a free-text value when custom
    custom: bool = False              # True = "Other": `candidate` is the reviewer's own typed value
    actor: str = "reviewer"


class AddReqBody(BaseModel):
    statement: str
    rtype: str = "functional"
    reason: str = ""  # where it came from (e.g. "Coverage gap" / "Possible miss")


class ResolveBody(BaseModel):
    kind: str = ""          # decision kind (conflict / disputed / gap / …)
    recommended: str = ""   # the verdict shown to the reviewer
    action: str = ""        # what was applied (keep-a / accept / reject / dismissed / …)
    actor: str = "reviewer"


class HandoffZipBody(BaseModel):
    asset1: list[str] = []   # local file paths (selected in the browser) -> asset1/ (design elements)
    asset2: list[str] = []   # local file paths -> asset2/ (static images for web)


def _needs_attention(r: Requirement) -> bool:
    """A requirement a human should look at — anything the triage does not rate 'routine'
    (inferred, flagged, low-confidence, conflicting, coarsely-traced, etc.)."""
    return needs_attention(r)


def _infer_type(filename: str) -> str:
    return _EXT_TYPE.get(Path(filename).suffix.lower(), "brd")


def _human_size(n: int | None) -> str:
    """Bytes -> a compact human-readable size for the input-file metadata view."""
    if n is None:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _corpus_files(base: Path, docs: list[dict]) -> list[dict]:
    """Per-file metadata for a corpus so the UI can show the user exactly what they are ingesting
    (name, kind, format, size). Reads the manifest's `docs` and stats the real files on disk."""
    files = []
    for doc in docs:
        rel = doc.get("file") or ""
        name = Path(rel).name if rel else str(doc.get("doc_id", "") or "document")
        fpath = (base / rel) if rel else None
        size = fpath.stat().st_size if (fpath and fpath.exists()) else None
        files.append({
            "doc_id": doc.get("doc_id", ""),
            "name": name,
            "type": doc.get("source_type") or _infer_type(name),
            "ext": (Path(name).suffix.lower().lstrip(".") or "—"),
            "size_bytes": size,
            "size": _human_size(size),
        })
    return files


def _req_view(r: Requirement) -> dict:
    """UI projection of a requirement — includes the source quote(s) so a reviewer always
    decides WITH the evidence in front of them."""
    return {
        "id": r.id, "statement": r.statement, "rtype": r.rtype.value,
        "feature": r.feature, "nfr_category": r.nfr_category,
        "priority": r.priority.value if r.priority else None,
        "status": r.status.value, "inferred": r.inferred, "confidence": r.confidence,
        "rationale": r.rationale, "quality": r.quality.model_dump(),
        "conflicts_with": r.conflicts_with,
        "owner": owner_of(r.statement, r.feature, r.rtype.value, r.nfr_category),
        "triage": triage(r),
        "sources": [
            {"doc_id": s.doc_id, "source_type": s.source_type, "location": s.location,
             "quote": s.raw_quote, "start": s.start, "end": s.end}
            for s in r.source_refs
        ],
    }


def _provider_name(prov) -> str:
    return getattr(getattr(prov, "inner", prov), "name", "mock") if prov else "mock"


def create_app(
    repo, *, db=None, frontend_dir: str | Path | None = None, provider=None,
    corpora_root: str = "datasets", uploads_root: str = "data/uploads",
) -> FastAPI:
    """Build the app around a Repository. `provider` (optional) is the LLM used when the UI
    runs the pipeline / generates; without it the app still serves review + deterministic
    generation. Pass `db=` for `rga serve` so the async engine lives in the app's own loop."""
    lifespan = None
    if db is not None:
        @asynccontextmanager
        async def lifespan(_app):  # noqa: F811
            await db.init()
            yield
            await db.dispose()

    app = FastAPI(title="RGA API", version="0.2.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.repo = repo
    app.state.provider = provider
    app.state.corpora_root = corpora_root
    app.state.uploads_root = uploads_root
    app.state.jobs = {}       # pid -> {state, stage, message, counts, ...}  (extraction run)
    app.state.genjobs = {}    # pid -> {state, stage, message, ...}          (generation run)
    app.state.artifacts = {}  # pid -> {filename: content}

    # ---- info -------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/config")
    async def config() -> dict:
        return {"provider": _provider_name(provider), "default_project": "P-ELAMS"}

    @app.get("/api/corpora")
    async def corpora() -> dict:
        out = []
        for kind, root in (("dataset", Path(corpora_root)), ("upload", Path(uploads_root))):
            if not root.exists():
                continue
            for d in sorted(p for p in root.iterdir() if p.is_dir()):
                man = d / "manifest.json"
                if not man.exists():
                    continue
                try:
                    docs = json.loads(man.read_text(encoding="utf-8")).get("docs", [])
                except Exception:
                    docs = []
                files = _corpus_files(d, docs)
                out.append({"id": d.name, "path": d.as_posix(),
                            "docs": [f["doc_id"] for f in files], "files": files, "kind": kind})
        return {"corpora": out}

    # ---- input: upload ----------------------------------------------------
    @app.post("/api/projects/{pid}/upload")
    async def upload(pid: str, files: list[UploadFile] = File(...)) -> dict:
        base = Path(uploads_root) / safe_dir_component(pid)
        docs_dir = base / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        docs = []
        for f in files:
            name = Path(f.filename or "document").name
            (docs_dir / name).write_bytes(await f.read())
            docs.append({"doc_id": Path(name).stem, "source_type": _infer_type(name), "file": f"docs/{name}"})
        (base / "manifest.json").write_text(json.dumps({"domain": pid, "docs": docs}, indent=2), encoding="utf-8")
        return {"corpus": base.as_posix(), "docs": [d["doc_id"] for d in docs]}

    # ---- run the pipeline (background) ------------------------------------
    async def _run_pipeline(pid: str, body: RunBody) -> None:
        from ..agents.analysis import run_analysis   # the SHARED analysis phase (graph + API use it)
        from ..agents.pipeline import extract_document
        from ..ingest.pipeline import ingest_corpus_to_store

        job = app.state.jobs[pid]

        def progress(stage: str, message: str, **extra) -> None:
            job.update(stage=stage, message=message, **extra)
            log.info("run[%s] %s — %s", pid, stage, message)

        cache0 = (getattr(provider, "hits", 0), getattr(provider, "misses", 0))  # for the per-run cache delta
        try:
            await repo.save_project(Project(id=pid, name=pid, status="INGESTED"))
            progress("ingesting", "Reading & structuring the documents…")
            ing = await ingest_corpus_to_store(body.corpus, repo, pid)
            n_chunks = sum(len(v) for v in ing.values())

            progress("extracting",
                     f"Extraction{' + Critic' if body.run_critic else ''} agent(s) reading {n_chunks} chunks…",
                     n_chunks=n_chunks)
            chunks = await repo.list_project_chunks(pid)
            # extraction is blocking (LLM calls + thread pool) -> off the event loop
            reqs, open_q = await asyncio.to_thread(
                extract_document, provider, chunks, pid,
                max_passes=body.max_passes, run_critic=body.run_critic, max_workers=body.max_workers,
            )
            log.info("run[%s] extracted %d candidate requirement(s)", pid, len(reqs))

            # SHARED analysis phase (identical for the CLI graph and the API — no divergence).
            # Runs off the event loop; `progress` still fires live from inside it for the UI.
            res = await asyncio.to_thread(
                run_analysis, provider, reqs, open_q, chunks,
                consolidate_llm=body.consolidate, adversarial=body.adversarial_verify,
                analyze_llm=False, progress=progress,
            )
            reqs, open_q, conflicts = res.reqs, res.open_q, res.conflicts
            log.info("run[%s] analysis: %d canonical, %d conflict(s), coverage %.1f%%",
                     pid, len(reqs), len(conflicts), res.coverage.get("accounted_pct", 0.0))

            # §7 technology stack — adopt a source-stated stack, else propose two options with a
            # recommendation. Stored on the run so the review screen can show it and the SRS §7 is
            # rebuilt from it. Off the event loop (LLM); deterministic fallback when no provider.
            from ..agents.techstack import analyze_tech_stack
            progress("analyzing", "Determining the technology stack (adopt from inputs or propose options)…")
            tech_stack = await asyncio.to_thread(
                analyze_tech_stack, provider, reqs, chunks, project_name=pid.strip())

            # persist the analysed requirements
            progress("analyzing", f"Saving {len(reqs)} analysed requirements…", n_extracted=len(reqs))
            for r in reqs:
                await repo.save_requirement(r)

            # #1 auto-approve the clean, confident, ROUTINE requirements so they never enter the human
            # queue (guarded: routine triage + confidence bar + not in a conflict). The gate still
            # blocks generation until the NON-routine items are triaged; a spot-check (#8) samples these.
            auto_ids: list[str] = []
            if body.auto_approve:
                for r in reqs:
                    if (triage(r)["level"] == "routine" and r.confidence >= body.auto_approve_bar
                            and not r.conflicts_with):
                        await apply_decision(repo, r.id, ReviewAction.accept, actor="auto-approve")
                        auto_ids.append(r.id)
                if auto_ids:
                    progress("analyzing",
                             f"Auto-approved {len(auto_ids)} routine requirement(s); "
                             f"{len(reqs) - len(auto_ids)} routed to review.")

            await repo.log_agent_run(AgentRun(
                project_id=pid, agent="A1+A0", provider=_provider_name(provider),
                status="success", input={"chunks": n_chunks},
                output={"accepted": len(reqs), "open_questions_count": len(open_q),
                        "open_questions": open_q, "conflicts": len(conflicts),
                        "auto_approved_ids": auto_ids, "tech_stack": tech_stack.model_dump()},
            ))

            cnts = counts(await repo.list_requirements(pid))
            hits = getattr(provider, "hits", 0) - cache0[0]
            misses = getattr(provider, "misses", 0) - cache0[1]
            cache_note = ""
            if hits or misses:
                total = hits + misses
                cache_note = f" LLM cache: {hits}/{total} calls reused ({round(100 * hits / total)}%)."
                log.info("run[%s] LLM cache this run: %d hits, %d misses", pid, hits, misses)
            job.update(state="done", stage="done", counts=cnts, open_questions=len(open_q),
                       conflicts=len(conflicts), cache={"hits": hits, "misses": misses},
                       message=f"Done — {len(reqs)} requirements, {len(conflicts)} conflict(s) flagged.{cache_note} Ready for review.")
            log.info("run[%s] DONE — %d requirements, %d conflicts, counts=%s", pid, len(reqs), len(conflicts), cnts)
        except Exception as exc:  # surface the failure to the UI
            log.exception("run[%s] FAILED", pid)
            job.update(state="error", stage="error", message=f"{type(exc).__name__}: {exc}")

    @app.post("/api/projects/{pid}/run")
    async def run(pid: str, body: RunBody) -> dict:
        job = app.state.jobs.get(pid)
        if job and job.get("state") == "running":
            raise HTTPException(status_code=409, detail="a pipeline run is already in progress")
        if provider is None:
            raise HTTPException(status_code=400, detail="no LLM provider configured; start the server with `rga serve --provider foundry`")
        app.state.jobs[pid] = {"state": "running", "stage": "starting", "message": "Starting…", "counts": {}}
        asyncio.create_task(_run_pipeline(pid, body))
        return {"started": True}

    @app.get("/api/projects/{pid}/status")
    async def status(pid: str) -> dict:
        return app.state.jobs.get(pid, {"state": "idle", "stage": "idle", "message": ""})

    # ---- review -----------------------------------------------------------
    @app.get("/api/projects/{pid}/requirements")
    async def list_requirements(pid: str, include: str = Query("all", pattern="^(all|pending|approved)$")) -> dict:
        reqs = await repo.list_requirements(pid)
        if include == "approved":
            shown = approved_only(reqs)
        elif include == "pending":
            shown = [r for r in reqs if r.status in PENDING]
        else:
            shown = reqs
        return {"project_id": pid, "counts": counts(reqs), "triage": triage_summary(reqs),
                "requirements": [_req_view(r) for r in shown]}

    @app.get("/api/projects/{pid}/gate")
    async def gate(pid: str) -> dict:
        reqs = await repo.list_requirements(pid)
        ok, reason = ready_for_generation(reqs)
        return {"ready": ok, "reason": reason, "counts": counts(reqs)}

    @app.get("/api/projects/{pid}/decisions")
    async def decisions(pid: str) -> dict:
        """Review by DECISION, not by requirement (#3, #6, #7): clustered, owner-routed, each with a
        proposed resolution + options + evidence + the requirements it affects."""
        from ..agents.decisions import build_decisions, decision_summary

        reqs = await repo.list_requirements(pid)
        out = await repo.latest_agent_run_output(pid)
        oq = (out or {}).get("open_questions", [])
        ds = build_decisions(reqs, oq)
        resolved_ids = await repo.resolved_decision_ids(pid)          # durable, survives reload (F3)
        for d in ds:
            d["resolved"] = d["id"] in resolved_ids
        open_ds = [d for d in ds if not d["resolved"]]
        return {
            "decisions": ds,
            "open": len(open_ds),
            "resolved": len(ds) - len(open_ds),
            "summary": decision_summary(open_ds),
        }

    @app.get("/api/projects/{pid}/spot-check")
    async def spot_check(pid: str, rate: float = 0.05) -> dict:
        """QA sample (#8): a deterministic ~rate slice of the AUTO-APPROVED requirements, so a human
        spot-checks them instead of reviewing all. Returns each with its evidence."""
        out = await repo.latest_agent_run_output(pid)
        auto_ids = (out or {}).get("auto_approved_ids", [])
        reqs = {r.id: r for r in await repo.list_requirements(pid)}
        step = max(1, round(1 / rate)) if rate and rate > 0 else 1
        sample = [_req_view(reqs[i]) for i in sorted(auto_ids)[::step] if i in reqs]
        return {"auto_approved": len(auto_ids), "sample_rate": rate, "sample": sample}

    @app.get("/api/projects/{pid}/calibration")
    async def calibration(pid: str) -> dict:
        """Acceptance rate per confidence band (#9) from the logged HUMAN decisions, plus a suggested
        auto-approve bar (lower it as a band proves reliable — the queue shrinks itself over time)."""
        reqs = await repo.list_requirements(pid)
        bands = [("0.90-1.00", 0.90, 1.01), ("0.75-0.90", 0.75, 0.90),
                 ("0.50-0.75", 0.50, 0.75), ("<0.50", -0.01, 0.50)]
        tally = {b[0]: [0, 0] for b in bands}  # [accepted-by-human, decided-by-human]
        for r in reqs:
            decs = [d for d in await repo.list_review_decisions(r.id)
                    if getattr(d, "actor", "") != "auto-approve"]
            if not decs:
                continue
            accepted = decs[-1].action == ReviewAction.accept
            for name, lo, hi in bands:
                if lo <= r.confidence < hi:
                    tally[name][1] += 1
                    tally[name][0] += 1 if accepted else 0
                    break
        out_bands = {name: {"accepted": a, "decided": t,
                            "acceptance_rate": (round(a / t, 3) if t else None)}
                     for name, (a, t) in ((n, tally[n]) for n, _, _ in bands)}
        suggested = 0.9
        for name, lo, _ in bands:
            a, t = tally[name]
            if t >= 10 and a / t >= 0.97:
                suggested = lo
        return {"bands": out_bands, "suggested_auto_approve_bar": suggested}

    @app.get("/api/requirements/{rid}")
    async def get_requirement(rid: str) -> dict:
        r = await repo.get_requirement(rid)
        if r is None:
            raise HTTPException(status_code=404, detail=f"unknown requirement '{rid}'")
        decisions = await repo.list_review_decisions(rid)
        return {"requirement": _req_view(r), "decisions": [d.model_dump(mode="json") for d in decisions]}

    @app.post("/api/requirements/{rid}/review")
    async def review(rid: str, body: ReviewBody) -> dict:
        try:
            r, decision = await apply_decision(repo, rid, body.action, edits=body.edits, actor=body.actor)
        except ReviewError as exc:
            code = 404 if str(exc).startswith("unknown requirement") else 400
            raise HTTPException(status_code=code, detail=str(exc)) from exc
        return {"requirement": _req_view(r), "decision": decision.model_dump(mode="json")}

    @app.post("/api/projects/{pid}/auto-accept")
    async def auto_accept(pid: str, body: AutoAcceptBody) -> dict:
        """Review-by-exception: auto-approve the 'safe' candidates (high-confidence, no flags,
        not inferred) with a logged decision, leaving only the ones that need attention for a
        human. Nothing is deleted — every auto-accept is recorded in the decision log."""
        reqs = await repo.list_requirements(pid)
        accepted = 0
        for r in reqs:
            if r.status in PENDING and not _needs_attention(r) and r.confidence >= body.min_confidence:
                await apply_decision(repo, r.id, ReviewAction.accept, actor=body.actor)
                accepted += 1
        remaining = sum(1 for r in await repo.list_requirements(pid) if r.status in PENDING)
        return {"accepted": accepted, "remaining_for_review": remaining, "min_confidence": body.min_confidence}

    @app.post("/api/projects/{pid}/accept-all")
    async def accept_all(pid: str) -> dict:
        """Approve EVERY pending requirement in one action (each logged). Blunt — bypasses
        per-item review — so the UI confirms before calling it."""
        accepted = 0
        for r in await repo.list_requirements(pid):
            if r.status in PENDING:
                await apply_decision(repo, r.id, ReviewAction.accept, actor="accept-all")
                accepted += 1
        return {"accepted": accepted}

    @app.post("/api/projects/{pid}/review-bulk")
    async def review_bulk(pid: str, body: BulkBody) -> dict:
        """Apply one decision (accept/reject) to many requirements at once."""
        done = 0
        for rid in body.ids:
            try:
                await apply_decision(repo, rid, body.action, actor=body.actor)
                done += 1
            except ReviewError:
                pass
        return {"count": done, "action": body.action.value}

    @app.post("/api/projects/{pid}/requirements")
    async def add_requirement(pid: str, body: AddReqBody) -> dict:
        """Add a human-authored requirement (from a gap / possible-miss decision). It is created
        already APPROVED, traceable to the review action, so it flows into the SRS like any other."""
        import hashlib

        from ..agents.prioritize import prioritize
        from ..models import RType, SourceRef

        stmt = body.statement.strip()
        if not stmt:
            raise HTTPException(status_code=400, detail="statement is required")
        rtype = body.rtype if body.rtype in {t.value for t in RType} else "functional"
        rid = "HU-" + hashlib.sha1((stmt + pid).encode("utf-8")).hexdigest()[:8]
        r = Requirement(
            id=rid, project_id=pid, statement=stmt, rtype=RType(rtype),
            feature="Added during review" if rtype == "functional" else None,
            status=Status.approved, confidence=1.0,
            source_refs=[SourceRef(doc_id="human", source_type="analysis",
                                   location=body.reason or "Added during review", raw_quote=stmt)],
            provenance={"agent": "human", "added_in_review": True, "reason": body.reason},
        )
        r.priority, _ = prioritize(stmt, r.rtype, inferred=False)
        await repo.save_requirement(r)
        return {"added": True, "id": rid}

    @app.post("/api/projects/{pid}/decisions/apply-recommended")
    async def apply_recommended_decisions(pid: str) -> dict:
        """Apply EVERY still-open decision's recommended verdict in one action, and persist each
        resolution (so it survives a reload). Conflicts keep the recommended side and reject the
        other; scope calls INCLUDE (disputed/undecided) or EXCLUDE (out-of-scope/deferred) the
        requirements they affect; gaps / possible-misses are acknowledged for the reviewer to author
        individually — never auto-inserted as requirement text."""
        from ..agents.decisions import build_decisions
        from ..agents.pipeline import _polarity_conflict, _statement_similarity

        reqs = await repo.list_requirements(pid)
        by_id = {r.id: r for r in reqs}
        out = await repo.latest_agent_run_output(pid)
        oq = (out or {}).get("open_questions", [])
        ds = build_decisions(reqs, oq)
        already = await repo.resolved_decision_ids(pid)
        applied = {"conflicts": 0, "excluded": 0, "included": 0, "to_author": 0}
        _RESTATE_SIM = 0.62  # an approved req counts as the excluded item only if it RESTATES it

        async def _apply(rid: str, action: ReviewAction) -> None:
            try:
                await apply_decision(repo, rid, action, actor="apply-recommended")
            except ReviewError:
                pass

        def _restates_excluded(rid: str, excluded: list[str]) -> bool:
            """True only if approved requirement `rid` is essentially the SAME statement as the
            out-of-scope/deferred item — never merely a topical neighbour. This is what makes bulk
            exclusion recall-safe: a real, in-scope requirement that only shares a word with an
            exclusion note (e.g. 'delivery' / 'payment') is NEVER dropped."""
            req = by_id.get(rid)
            if req is None:
                return False
            return any(
                e and _statement_similarity(req.statement, e) >= _RESTATE_SIM
                and not _polarity_conflict(req.statement, e)
                for e in excluded
            )

        for d in ds:
            if d["id"] in already:
                continue  # idempotent — never re-apply an already-resolved decision
            kind, rec, affected = d["kind"], d.get("recommended", ""), d.get("affected", [])
            if kind == "conflict":
                a_id = affected[0] if len(affected) > 0 else None
                b_id = affected[1] if len(affected) > 1 else None
                keep, drop = (a_id, b_id) if rec.startswith("Keep A") else (b_id, a_id)
                if keep:
                    await _apply(keep, ReviewAction.accept)
                if drop:
                    await _apply(drop, ReviewAction.reject)
                action_label = f"keep {keep or '?'}, reject {drop or '?'}"
                applied["conflicts"] += 1
            elif kind in ("out_of_scope", "deferred"):
                # RECALL-SAFE: reject ONLY approved requirements that RESTATE the excluded item —
                # never the whole topical affected-set (which mass-drops real, in-scope requirements
                # that merely share a word). The exclusion NOTE is itself an open-question, already
                # absent from the SRS body; genuinely-contradicting firm reqs are handled by scope
                # reconciliation / conflict detection during analysis, not this blunt bulk action.
                excluded = d.get("evidence") or [d.get("question", "")]
                dropped = [rid for rid in affected if _restates_excluded(rid, excluded)]
                for rid in dropped:
                    await _apply(rid, ReviewAction.reject)
                action_label = f"excluded {len(dropped)} requirement(s) that restate the out-of-scope item"
                applied["excluded"] += len(dropped)
            elif kind in ("disputed", "undecided"):
                for rid in affected:
                    await _apply(rid, ReviewAction.accept)  # include is non-destructive (keeps reqs)
                action_label = f"included {len(affected)} affected requirement(s) in v1"
                applied["included"] += len(affected)
            else:  # gap / possible_miss — needs human authoring; do NOT auto-insert meta text
                action_label = "acknowledged — author the requirement individually"
                applied["to_author"] += 1
            await repo.save_decision_resolution(pid, d["id"], kind, rec, action_label, actor="apply-recommended")

        return {"applied": applied, "resolved_total": len(await repo.resolved_decision_ids(pid))}

    @app.post("/api/projects/{pid}/decisions/{decision_id}/resolve")
    async def resolve_decision(pid: str, decision_id: str, body: ResolveBody) -> dict:
        """Persist that ONE decision has been resolved (its requirement mutations, if any, are applied
        separately by the caller). Makes a per-card resolution durable + audited, so it does not
        reappear on reload (F3)."""
        await repo.save_decision_resolution(
            pid, decision_id, body.kind, body.recommended, body.action or "resolved", actor=body.actor,
        )
        return {"resolved": decision_id, "resolved_total": len(await repo.resolved_decision_ids(pid))}

    # ---- generate + artifacts --------------------------------------------
    async def _run_generate(pid: str) -> None:
        """Assemble the handoff pack in the background (the narrative LLM call can take a
        minute), reporting progress via genjobs so the UI never hangs on the request."""
        from ..generate.handoff import generate_handoff

        gj = app.state.genjobs[pid]

        def gprogress(stage: str, message: str, **extra) -> None:
            gj.update(stage=stage, message=message, **extra)
            log.info("generate[%s] %s — %s", pid, stage, message)

        try:
            reqs = await repo.list_requirements(pid)
            approved_n = len(approved_only(reqs))
            out = await repo.latest_agent_run_output(pid)
            oq = (out or {}).get("open_questions", [])
            tech_stack = (out or {}).get("tech_stack")
            # the reviewer's per-aspect tech-stack picks (aspect key -> candidate); §7 uses them,
            # falling back to the recommended candidate for any aspect not explicitly chosen
            ts_res = await repo.list_decision_resolutions(pid)
            ts_sel = {r["decision_id"].split("::", 1)[1]: r.get("action")
                      for r in ts_res
                      if str(r.get("decision_id", "")).startswith("tech-stack::") and r.get("action")}
            gprogress("assembling",
                      f"Drafting SRS narrative + assembling SRS/RTM for {approved_n} approved requirements…")
            pack = await asyncio.to_thread(
                generate_handoff, reqs, project_name=pid.strip(),
                date=datetime.date.today().isoformat(), provider=provider,
                open_questions=oq, run_narrative=(provider is not None),
                tech_stack=tech_stack, tech_stack_selection=ts_sel,
            )
            gprogress("writing", "Writing SRS, RTM, seed models, open-questions…")
            # Design-phase handoff pack = the cleaned SRS + cleaned RTM ONLY (Part J). Appendix C
            # lives inside the SRS; no seed-models file, no separate open-questions file, no diagrams.
            # manifest.json is operational metadata describing the pack, not a deliverable document.
            files = {
                "SRS.md": pack["srs_markdown"],
                "RTM.csv": pack["rtm_csv"],                      # RTM is delivered as CSV now
                "manifest.json": json.dumps(pack["manifest"], indent=2),
            }
            outdir = Path("handoff") / safe_dir_component(pid)
            outdir.mkdir(parents=True, exist_ok=True)
            # remove stale pack files from a previous (pre-alignment) run so the on-disk pack is
            # exactly {SRS, RTM, manifest} (Part J) — no leftover seed-models / open-questions files.
            for stale in ("open-questions.md", "seed-models.md", "open-questions.docx",
                          "seed-models.docx", "RTM.md", "RTM.docx"):   # RTM.md/.docx replaced by RTM.csv
                (outdir / stale).unlink(missing_ok=True)
            for name, content in files.items():
                (outdir / name).write_text(content, encoding="utf-8")
            # also emit Word (.docx) versions of each document (best-effort; never breaks the .md)
            from ..generate.docx_export import write_docx_versions

            docx_names = write_docx_versions(outdir, {k: v for k, v in files.items() if k.endswith(".md")})
            app.state.artifacts[pid] = files
            gj.update(state="done", stage="done", count=approved_n,
                      files=list(files.keys()) + docx_names,
                      out_dir=str(outdir), manifest=pack["manifest"],
                      message=f"Generated {len(files)} files (+{len(docx_names)} Word .docx) from {approved_n} approved requirements.")
            log.info("generate[%s] DONE — wrote %s (+docx %s) to %s", pid, list(files.keys()), docx_names, outdir)
        except Exception as exc:
            log.exception("generate[%s] FAILED", pid)
            gj.update(state="error", stage="error", message=f"{type(exc).__name__}: {exc}")

    @app.post("/api/projects/{pid}/generate")
    async def generate(pid: str) -> dict:
        """Start generation in the BACKGROUND (returns immediately). Poll /generate-status;
        fetch the docs from /artifacts when done. Refuses if the gate is closed or a run is
        already in progress."""
        gj = app.state.genjobs.get(pid)
        if gj and gj.get("state") == "running":
            raise HTTPException(status_code=409, detail="generation already in progress")
        reqs = await repo.list_requirements(pid)
        ok, reason = ready_for_generation(reqs)
        if not ok:
            raise HTTPException(status_code=409, detail=reason)
        app.state.genjobs[pid] = {"state": "running", "stage": "starting", "message": "Starting generation…"}
        log.info("generate[%s] started (%d approved)", pid, len(approved_only(reqs)))
        asyncio.create_task(_run_generate(pid))
        return {"started": True}

    @app.get("/api/projects/{pid}/generate-status")
    async def generate_status(pid: str) -> dict:
        return app.state.genjobs.get(pid, {"state": "idle", "stage": "idle", "message": ""})

    # ---- technology stack (SRS §7) — review-screen decision ----------------
    def _ts_selections(resolutions: list[dict]) -> dict[str, str]:
        """Per-aspect picks recovered from the resolution log: aspect key -> chosen candidate."""
        return {r["decision_id"].split("::", 1)[1]: r.get("action")
                for r in resolutions
                if str(r.get("decision_id", "")).startswith("tech-stack::") and r.get("action")}

    @app.get("/api/projects/{pid}/tech-stack")
    async def get_tech_stack(pid: str) -> dict:
        """The tech-stack analysis from the run (for the review screen): per-aspect candidates (a
        stack adopted from the inputs, or popular candidates with one recommended per aspect) plus
        the reviewer's per-aspect selections so far. Switched-off aspects (Payments, Hosting /
        Infrastructure) are filtered out here too, so the review screen matches §7 even for a stored
        run that still contains them."""
        from ..agents.techstack import is_excluded_aspect

        out = await repo.latest_agent_run_output(pid)
        ts = (out or {}).get("tech_stack")
        if isinstance(ts, dict) and ts.get("aspects"):
            ts = {**ts, "aspects": [a for a in ts["aspects"]
                                    if not is_excluded_aspect(a.get("key", ""), a.get("title", ""))]}
        selections = _ts_selections(await repo.list_decision_resolutions(pid))
        return {"tech_stack": ts, "selections": selections}

    @app.post("/api/projects/{pid}/tech-stack/select")
    async def select_tech_stack(pid: str, body: TechStackSelectBody) -> dict:
        """Record the reviewer's chosen candidate for ONE aspect; §7 renders that choice on the next
        generate. A stack stated in the inputs is adopted, not selectable."""
        out = await repo.latest_agent_run_output(pid)
        ts = (out or {}).get("tech_stack") or {}
        if ts.get("stated_in_inputs"):
            raise HTTPException(status_code=409, detail="the stack is stated in the inputs; nothing to select")
        aspect = next((a for a in ts.get("aspects", []) if a.get("key") == body.aspect), None)
        if aspect is None:
            raise HTTPException(status_code=400, detail=f"unknown aspect '{body.aspect}'")
        if body.custom:
            # "Other" — the reviewer typed their own technology; accept any non-empty, sane value.
            chosen = (body.candidate or "").strip()
            if not chosen:
                raise HTTPException(status_code=400, detail="a custom (Other) technology must not be empty")
            if len(chosen) > 80:
                raise HTTPException(status_code=400, detail="custom technology name is too long (max 80 characters)")
        else:
            names = [c.get("name") for c in aspect.get("candidates", [])]
            if body.candidate not in names:
                raise HTTPException(status_code=400, detail=f"unknown candidate for '{body.aspect}'; choose one of {names}")
            chosen = body.candidate
        rec = next((c.get("name") for c in aspect.get("candidates", []) if c.get("recommended")), "")
        await repo.save_decision_resolution(
            project_id=pid, decision_id=f"tech-stack::{body.aspect}", kind="tech_stack",
            recommended=rec, action=chosen, actor=body.actor)
        return {"aspect": body.aspect, "selected": chosen, "recommended": rec, "custom": body.custom}

    # ---- reset / delete ---------------------------------------------------
    @app.delete("/api/projects/{pid}")
    async def delete_project(pid: str) -> dict:
        """Clear ALL data for one project (requirements, chunks, decisions, runs)."""
        removed = await repo.delete_project(pid)
        app.state.jobs.pop(pid, None)
        app.state.artifacts.pop(pid, None)
        return {"deleted": True, "project": pid, "requirements_removed": removed}

    @app.post("/api/reset")
    async def reset() -> dict:
        """Wipe the ENTIRE store (all projects) and recreate an empty schema."""
        await repo.db.reset()
        app.state.jobs.clear()
        app.state.artifacts.clear()
        return {"reset": True}

    @app.get("/api/projects/{pid}/artifacts")
    async def list_artifacts(pid: str) -> dict:
        return {"files": list(app.state.artifacts.get(pid, {}).keys())}

    @app.get("/api/projects/{pid}/artifacts/{name}", response_class=PlainTextResponse)
    async def get_artifact(pid: str, name: str):
        # binary Word docs are served from disk as a download; text (.md/.json) is served inline
        if name.endswith(".docx"):
            p = Path("handoff") / safe_dir_component(pid) / name
            if not p.exists():
                raise HTTPException(status_code=404, detail=f"no artifact '{name}'")
            return Response(
                content=p.read_bytes(),
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
        content = app.state.artifacts.get(pid, {}).get(name)
        if content is None:
            p = Path("handoff") / safe_dir_component(pid) / name
            if not p.exists():
                raise HTTPException(status_code=404, detail=f"no artifact '{name}'")
            content = p.read_text(encoding="utf-8")
        if name.endswith(".csv"):
            # correct MIME + filename so the browser saves it as .csv (not .txt); the RTM preview
            # still reads it fine via fetch(). text/plain with no filename made browsers append .txt.
            return Response(content=content, media_type="text/csv",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})
        return content

    # ---- local file browser + ZIP handoff --------------------------------
    # RGA runs on the user's own machine (bound to 127.0.0.1), so these endpoints let the UI browse
    # the LOCAL filesystem and select files BY PATH — the backend reads them off disk directly. No
    # browser upload happens (some orgs block uploads); only path strings cross the wire.
    _IMG_EXT = {"jpg", "jpeg", "png", "gif", "svg", "webp", "bmp", "ico", "tiff", "avif"}

    @app.get("/api/fs/roots")
    async def fs_roots() -> dict:
        """Starting points for the file browser: home + common folders, and drives on Windows."""
        import os
        import string

        home = Path.home()
        roots = [{"name": "Home", "path": str(home)}]
        for special in ("Desktop", "Documents", "Downloads", "Pictures"):
            if (home / special).is_dir():
                roots.append({"name": special, "path": str(home / special)})
        if os.name == "nt":
            for d in string.ascii_uppercase:
                drive = Path(f"{d}:\\")
                if drive.exists():
                    roots.append({"name": f"{d}:\\", "path": str(drive)})
        else:
            roots.append({"name": "/", "path": "/"})
        return {"roots": roots, "cwd": str(Path.cwd())}

    @app.get("/api/fs/list")
    async def fs_list(path: str = Query(...)) -> dict:
        """List a directory on the local machine (folders first, then files) for the file browser."""
        try:
            p = Path(path).expanduser().resolve()
        except (OSError, ValueError, RuntimeError):
            raise HTTPException(status_code=400, detail="invalid path")
        if not p.is_dir():
            raise HTTPException(status_code=400, detail=f"not a directory: {path}")
        entries = []
        try:
            children = sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
        except (OSError, PermissionError) as exc:
            raise HTTPException(status_code=403, detail=f"cannot read directory: {exc}")
        for child in children[:2000]:                          # cap pathological dirs
            try:
                is_dir = child.is_dir()
                size = None if is_dir else child.stat().st_size
            except (OSError, PermissionError):
                continue                                        # skip entries we can't stat
            ext = child.suffix.lower().lstrip(".")
            entries.append({
                "name": child.name, "path": str(child), "is_dir": is_dir,
                "size": _human_size(size) if size is not None else "",
                "is_image": ext in _IMG_EXT,
            })
        parent = str(p.parent) if p.parent != p else None
        return {"path": str(p), "parent": parent, "entries": entries}

    @app.post("/api/projects/{pid}/handoff-zip")
    async def handoff_zip(pid: str, body: HandoffZipBody):
        """Build the final handoff ZIP: the SRS/RTM pack + the files the user selected (by local path)
        under asset1/ and asset2/. Files are read from the local disk — nothing is uploaded. Refuses
        until the SRS has been generated for this project."""
        outdir = Path("handoff") / safe_dir_component(pid)
        if not (outdir / "SRS.docx").exists() and not (outdir / "SRS.md").exists():
            raise HTTPException(status_code=400,
                                detail="generate the SRS first — no handoff pack found for this project")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("SRS.docx", "SRS.md", "RTM.csv", "manifest.json"):
                f = outdir / name
                if f.exists():
                    z.write(f, arcname=name)
            for folder, paths in (("asset1", body.asset1), ("asset2", body.asset2)):
                used: set[str] = set()
                for raw in paths:
                    src = Path(raw)
                    if not src.is_file():
                        continue                                # skip anything that isn't a real file
                    arc = src.name
                    n = 1
                    while arc in used:                          # keep names unique within the folder
                        arc = f"{src.stem}_{n}{src.suffix}"
                        n += 1
                    used.add(arc)
                    z.write(src, arcname=f"{folder}/{arc}")
        buf.seek(0)
        fname = f"{safe_dir_component(pid)}_handoff.zip"
        return Response(content=buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    # Serve the built frontend last (API routes above win for /api/*).
    if frontend_dir is not None:
        dist = Path(frontend_dir)
        if dist.is_dir():
            app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app

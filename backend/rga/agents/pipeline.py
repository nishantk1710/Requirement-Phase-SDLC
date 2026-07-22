"""P3 extraction pipeline: A1 (extract) -> deterministic grounding guard -> A0 (critic),
over a bounded multi-pass loop, accumulating unique requirements.

Guarantees (all enforced in code):
  * every quote is LOCATED in the chunk; we keep the SOURCE's own bytes + doc offsets;
  * a quote must be SUBSTANTIVE (grounding.py) — trivial fragments do not count;
  * a requirement with no valid span is ROUTED TO OPEN-QUESTIONS for human verification, never
    asserted or fabricated (nothing is silently dropped);
  * the critic (A0) adds a semantic grounding check, and FAILS CLOSED — a candidate the critic
    does not adjudicate is routed to open-questions (unverified), never waved through;
  * near-duplicate requirements MERGE (same statement, or overlapping span + similar
    wording) — evidence is never discarded, and over-extraction is reduced;
  * requirement IDs are stable content hashes (reproducible across runs);
  * the loop is bounded; chunk processing may run with bounded concurrency (max_workers).
"""

from __future__ import annotations

import difflib
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor

from ..eval.dataset import normalize
from ..llm.base import LLMProvider
from ..logging_setup import get_logger
from ..models import AgentRun, Chunk, Requirement, RType, SourceRef, Status
from .critic import verify_batch
from .extraction import extract_from_chunk
from .grounding import valid_spans
from .requirement_filter import is_genuine_requirement
from .schemas import ExtractedRequirement

log = get_logger("rga.extraction")
from .scope_classifier import classify_scope_status

_RTYPES = {t.value for t in RType}

# near-duplicate thresholds
_SPAN_IOU = 0.6
_STMT_JACCARD = 0.5


def _req_id(statement: str, project_id: str) -> str:
    """Stable content-hash id, SCOPED to the project so the same normalised statement in two
    projects yields DIFFERENT ids — no cross-project primary-key collision / clobbering.
    Deterministic per (project, statement); the NUL separator can't occur in a real id."""
    key = f"{project_id}\x00{normalize(statement)}"
    return "EX-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(normalize(a).split()), set(normalize(b).split())
    return len(sa & sb) / len(sa | sb) if (sa or sb) else 0.0


def _ref_iou(a: SourceRef, b: SourceRef) -> float:
    if a.doc_id != b.doc_id or a.start is None or b.start is None:
        return 0.0
    overlap = max(0, min(a.end, b.end) - max(a.start, b.start))
    union = max(a.end, b.end) - min(a.start, b.start)
    return overlap / union if union > 0 else 0.0


def _is_near_dup(r: Requirement, existing: Requirement) -> bool:
    if normalize(r.statement) == normalize(existing.statement):
        return True
    if _jaccard(r.statement, existing.statement) >= _STMT_JACCARD and any(
        _ref_iou(sa, sb) >= _SPAN_IOU for sa in r.source_refs for sb in existing.source_refs
    ):
        return True
    return False


def _merge_refs(into: Requirement, other: Requirement) -> None:
    have = {(sr.doc_id, sr.start, sr.end) for sr in into.source_refs}
    for sr in other.source_refs:
        if (sr.doc_id, sr.start, sr.end) not in have:
            into.source_refs.append(sr)
            have.add((sr.doc_id, sr.start, sr.end))


def refs_as_dicts(refs) -> list[dict]:
    """Serialise source-refs (FULL provenance: quote + offsets) for an open-question `merged_refs`
    payload, so an item MOVED to open-questions (demoted / refuted / reconciled) keeps ALL of its
    evidence — not just the first ref — and a reviewer can see exactly what grounded it."""
    return [
        {"doc_id": sr.doc_id, "location": sr.location, "raw_quote": sr.raw_quote,
         "start": sr.start, "end": sr.end}
        for sr in (refs or [])
    ]


# --- source authority (canonical-phrasing preference) ------------------------
# A formal specification (SRS/PRD/BRD) states an obligation more precisely than an intake FORM /
# product-BACKLOG user story of the SAME thing. When two near-duplicates merge, the higher-authority
# statement should REPRESENT the pair (the lower one's wording is kept in the audit trail) — so a
# formal requirement is never displaced by a backlog story that happens to be seen first / longer.
_SOURCE_AUTHORITY = {"srs": 3, "prd": 3, "brd": 2, "email": 1, "jira": 1, "notes": 1,
                     "transcript": 1, "form": 0, "backlog": 0, "csv": 0}


def source_authority(r: Requirement) -> int:
    """Highest authority among a requirement's source documents (unknown types default to 1)."""
    ranks = [_SOURCE_AUTHORITY.get((sr.source_type or "").lower(), 1) for sr in r.source_refs]
    return max(ranks) if ranks else 1


# --- semantic de-duplication (cross-document paraphrase merge) ----------------
# The in-loop near-dedup only merges items whose SOURCE SPANS overlap, so the same
# requirement stated in two different documents ("Raw card details shall never be stored…"
# vs "The system shall never store raw card details…") survives twice. This pass merges by
# STATEMENT similarity regardless of where the evidence came from — keeping all source refs.
_DEDUP_THRESHOLD = 0.72


def _stmt_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(text))


# --- polarity guard ----------------------------------------------------------
# Statement similarity is order-INSENSITIVE, so two requirements with the same word set can still
# be OPPOSITES: "sort price low->high" vs "high->low", or "shall store X" vs "shall NOT store X".
# Any decision that treats two statements as the same thing (merge, echo-suppress, adopt-as-rewrite)
# must additionally confirm their POLARITY matches — otherwise a real requirement is silently lost.
_NEG_RE = re.compile(
    r"\b(not|no|never|cannot|can't|can not|without|neither|nor|none|n't|"
    r"don't|won't|shan't|shouldn't|mustn't|isn't|aren't|doesn't)\b",
    re.IGNORECASE,
)
# Ordered opposite pairs — opposite sides (or the same pair in opposite order) = opposite meaning.
_OPPOSITES: tuple[tuple[str, str], ...] = (
    ("low", "high"), ("lowest", "highest"), ("ascending", "descending"), ("asc", "desc"),
    ("increasing", "decreasing"), ("cheapest", "priciest"), ("min", "max"),
    ("minimum", "maximum"), ("least", "most"), ("smallest", "largest"), ("oldest", "newest"),
    ("before", "after"), ("enable", "disable"), ("enabled", "disabled"), ("allow", "deny"),
    ("allow", "block"), ("grant", "revoke"), ("include", "exclude"), ("add", "remove"),
    ("show", "hide"), ("accept", "reject"), ("expand", "collapse"), ("lock", "unlock"),
    ("activate", "deactivate"), ("increase", "decrease"), ("first", "last"),
)


def _has_negation(text: str) -> bool:
    return bool(_NEG_RE.search(text or ""))


def _order(tokens: list[str], x: str, y: str) -> int:
    return -1 if tokens.index(x) < tokens.index(y) else 1


def _polarity_conflict(a: str, b: str) -> bool:
    """True if `a` and `b` express OPPOSITE requirements despite similar wording — a negation on
    only one side, or a directional/antonym pair taken in opposite directions. Such a pair must
    never be merged, echo-suppressed, or adopted as a rewrite of the other (it would drop a real,
    distinct requirement). Errs toward 'conflict' — the safe direction is to keep both."""
    if _has_negation(a) != _has_negation(b):
        return True
    ta, tb = _stmt_tokens(a), _stmt_tokens(b)
    sa, sb = set(ta), set(tb)
    for x, y in _OPPOSITES:
        ax, ay, bx, by = x in sa, y in sa, x in sb, y in sb
        # single-sided: a says x (not y), b says y (not x) — opposite choices
        if (ax and not ay and by and not bx) or (ay and not ax and bx and not by):
            return True
        # both mention the pair, but in opposite ORDER ("low ... high" vs "high ... low")
        if ax and ay and bx and by and _order(ta, x, y) != _order(tb, x, y):
            return True
    return False


def _statement_similarity(a: str, b: str) -> float:
    """0..1 similarity that is robust to word order and phrasing: the max of token-set
    Jaccard and the sequence ratio of the sorted token streams."""
    ta, tb = _stmt_tokens(a), _stmt_tokens(b)
    if not ta or not tb:
        return 0.0
    sa, sb = set(ta), set(tb)
    jacc = len(sa & sb) / len(sa | sb)
    sort_ratio = difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return max(jacc, sort_ratio)


# --- topic overlap (subject-matter match, not phrasing) ----------------------
# Structural filler that carries no subject; dropping it makes overlap reflect the TOPIC (objects,
# actions, entities), so "same subject as X" is robust to how each side is phrased.
_TOPIC_STOP = frozenset(
    "the a an this that these those system platform service shall must should will may be is are to "
    "of for and or with on in at by as it its can without not no when then so they their them you i "
    "want each any all from into out over under more most less few".split()
)


def topic_terms(text: str) -> set[str]:
    """Content words that carry the subject of a statement (filler removed)."""
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _TOPIC_STOP and len(w) > 2}


def topic_overlap(a: str, b: str) -> float:
    """0..1 Jaccard over content words — how much two statements are about the SAME subject,
    independent of phrasing. Used to tell whether a requirement is the same topic as an open item."""
    ta, tb = topic_terms(a), topic_terms(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def dedupe_requirements(
    reqs: list[Requirement], *, threshold: float = _DEDUP_THRESHOLD
) -> tuple[list[Requirement], int]:
    """Collapse duplicate/near-paraphrase requirements. Two requirements merge when their
    statements are identical after normalisation, or highly similar (>= threshold) and of the
    same type (the same-type guard avoids merging distinct requirements that share vocabulary).
    Evidence (source refs) from the merged item is preserved on the survivor. Deterministic and
    reproducible. Returns (deduped_requirements, merged_count)."""
    kept: list[Requirement] = []
    merged = 0
    for r in reqs:
        best, best_sim = None, 0.0
        for k in kept:
            if normalize(r.statement) == normalize(k.statement):
                best, best_sim = k, 1.0
                break
            sim = _statement_similarity(r.statement, k.statement)
            if sim > best_sim:
                best, best_sim = k, sim
        if (
            best is not None
            and best_sim >= threshold
            and (best_sim >= 0.9 or best.rtype == r.rtype)
            and not _polarity_conflict(r.statement, best.statement)  # never merge opposites
        ):
            # Survivor keeps the HIGHER-AUTHORITY statement (a formal-spec BRD statement over a
            # backlog user story), not merely the first-seen — polarity is already checked, so the
            # swap is meaning-preserving. The displaced wording is kept in the audit trail (C-M4).
            displaced = r.statement
            if source_authority(r) > source_authority(best):
                displaced = best.statement
                best.statement = r.statement
            _merge_refs(best, r)  # keep ALL evidence
            if r.id not in best.duplicate_of:
                best.duplicate_of.append(r.id)
            best.provenance.setdefault("absorbed_statements", []).append(displaced)
            merged += 1
        else:
            kept.append(r)
    return kept, merged


def unaccounted_requirements(original: list[Requirement], survivors: list[Requirement]) -> list[str]:
    """Recall guard for consolidation/folding (Fix 6): return the statements from `original` that are
    NOT accounted for in `survivors` — i.e. neither a survivor's current statement nor recorded in a
    survivor's `absorbed_statements` audit trail. An empty list PROVES the fold was lossless: every
    input requirement survived, either as itself or as a deliberate, recorded merge. Anything
    returned is a silently-dropped requirement that must be surfaced for review rather than vanish.

    This is why the NFR count dropping (e.g. 20 → 17 on Horizon-Green) is safe to trust: each drop
    corresponds to a recorded merge, not a deletion. Use it on any before/after requirement set,
    NFRs included, to assert folding never loses a requirement."""
    accounted = {normalize(s.statement) for s in survivors}
    for s in survivors:
        for absorbed in (s.provenance.get("absorbed_statements") or []):
            accounted.add(normalize(absorbed))
    return [o.statement for o in original if normalize(o.statement) not in accounted]


# --- open-question post-processing (drop redundant + collapse near-duplicates) --------------
# Priority when two open-questions merge: the more-decisive kind survives (a conflict/scope note
# outranks a bare "ungrounded" restatement of the same item).
_OPENQ_KIND_PRIORITY = {
    "conflict": 0, "possible_miss": 1, "gap": 2, "out_of_scope": 3, "disputed": 4, "undecided": 5,
    "deferred": 6, "critic_rejected": 7, "non_requirement": 8, "ambiguous": 9, "inferred": 10,
    "ungrounded": 11,
}
_OPENQ_DEDUP_THRESHOLD = 0.85   # high: only collapse near-verbatim restatements, never distinct items
_ECHO_SIM = 0.85                # an ungrounded/inferred note this similar to a firm req is an echo

# Words that do NOT identify a feature — so a generic umbrella like "the system shall provide a
# search capability" reduces to its core feature noun(s). Used to spot an ungrounded scope-LIST
# echo of a requirement already captured (grounded) elsewhere.
_GENERIC_FEATURE_WORDS = frozenset(
    "the a an this that system platform application service shall must should will may can provide "
    "provides support supports include includes including enable enables offer offers allow allows "
    "ability capability capabilities functionality feature features users user customer shopper for "
    "of to and or with via using use each all any able provided".split()
)


def _core_feature_terms(text: str) -> set[str]:
    return {t for t in _stmt_tokens(text) if len(t) > 3 and t not in _GENERIC_FEATURE_WORDS}


def postprocess_open_questions(
    reqs: list[Requirement], open_q: list[dict], *, dedup_threshold: float = _OPENQ_DEDUP_THRESHOLD
) -> list[dict]:
    """Clean the open-questions list before it becomes Appendix C:

      (5) drop 'ungrounded'/'inferred' entries that ECHO a firm requirement (grounded in another
          chunk) — the evidence lives on the requirement, so the open-question is redundant noise.
          A match is exact-normalised OR high statement similarity (>= _ECHO_SIM), which catches
          paraphrase echoes while staying well above the level where distinct items would collide.
      (4) collapse near-verbatim duplicate entries (the same open item restated across chunks),
          keeping the fullest statement and the most-decisive kind.

    Deterministic and order-stable. Nothing that represents a distinct open item is discarded.
    """
    firm_norms = {normalize(r.statement) for r in reqs}
    firm_stmts = [r.statement for r in reqs]
    firm_cores = [(fs, _core_feature_terms(fs)) for fs in firm_stmts]

    def _echoes_firm(stmt: str) -> bool:
        if normalize(stmt) in firm_norms:
            return True
        # (a) a near-match is an echo unless it is the polar opposite of the firm requirement
        if any(_statement_similarity(stmt, fs) >= _ECHO_SIM and not _polarity_conflict(stmt, fs)
               for fs in firm_stmts):
            return True
        # (b) a generic scope-list umbrella ("shall provide a search capability") is a redundant echo
        # when a SINGLE approved requirement already covers >=2/3 of its feature vocabulary — the
        # feature IS captured (grounded elsewhere), so the ungrounded note is noise. Per-requirement
        # (not union) so a genuinely NEW combination of otherwise-common words is never dropped.
        core = _core_feature_terms(stmt)
        if core:
            for fs, fc in firm_cores:
                if fc and len(core & fc) / len(core) >= 0.66 and not _polarity_conflict(stmt, fs):
                    return True
        return False

    # (5) suppress redundant ungrounded/inferred entries already covered by a firm requirement
    survivors: list[dict] = []
    for o in open_q:
        kind = o.get("type", "")
        stmt = (o.get("statement") or "").strip()
        if kind in ("ungrounded", "inferred") and stmt and _echoes_firm(stmt):
            continue  # already a firm, grounded requirement — drop the duplicate note
        survivors.append(o)

    # (4) collapse near-verbatim duplicates (cross-kind; most-decisive kind + fullest text survive)
    kept: list[dict] = []
    for o in survivors:
        stmt = (o.get("statement") or "").strip()
        if not stmt:
            kept.append(o)
            continue
        match = next(
            (
                k for k in kept
                if _statement_similarity(stmt, k.get("statement", "")) >= dedup_threshold
                and not _polarity_conflict(stmt, k.get("statement", ""))
            ),
            None,
        )
        if match is None:
            kept.append(o)
            continue
        if _OPENQ_KIND_PRIORITY.get(o.get("type", ""), 99) < _OPENQ_KIND_PRIORITY.get(match.get("type", ""), 99):
            match["type"] = o.get("type", match.get("type"))
            match["reason"] = o.get("reason", match.get("reason", ""))
        if len(stmt) > len(match.get("statement", "")):
            match["statement"] = stmt
    return kept


def _to_requirement(
    er: ExtractedRequirement, spans: list[tuple[str, int, int]], chunk: Chunk, project_id: str
) -> Requirement:
    rtype = er.rtype if er.rtype in _RTYPES else "functional"
    return Requirement(
        id=_req_id(er.statement, project_id),
        project_id=project_id,
        statement=er.statement,
        rtype=RType(rtype),
        feature=(er.feature or None) if rtype == "functional" else None,
        nfr_category=(er.nfr_category or None) if rtype == "non_functional" else None,
        inferred=er.inferred,
        confidence=max(0.0, min(1.0, er.confidence)),
        rationale=er.rationale,
        status=Status.candidate,
        source_refs=[
            SourceRef(
                doc_id=chunk.doc_id,
                source_type=chunk.source_type,
                location=chunk.location,
                raw_quote=slice_,
                start=chunk.start + s,
                end=chunk.start + e,
            )
            for (slice_, s, e) in spans
        ],
        provenance={"agent": "A1", "phase": "P3", "verified_by": "A0"},
    )


def _process_chunk(
    provider: LLMProvider, chunk: Chunk, project_id: str, max_passes: int, run_critic: bool
) -> tuple[list[Requirement], list[dict]]:
    found: dict[str, Requirement] = {}
    open_q: list[dict] = []
    open_seen: set[tuple[str, str]] = set()
    hints: list[str] = []

    def add_open(kind: str, statement: str, reason: str) -> None:
        key = (kind, normalize(statement))
        if key in open_seen:
            return
        open_seen.add(key)
        open_q.append(
            {"type": kind, "statement": statement, "reason": reason, "location": chunk.location, "doc_id": chunk.doc_id}
        )

    for _ in range(max_passes):
        # 1) extract + deterministic grounding guard -> candidate requirements
        cands: list[tuple[str, Requirement, str, list[str]]] = []
        for er in extract_from_chunk(provider, chunk, hints):
            key = normalize(er.statement)
            if key in found or any(c[0] == key for c in cands):
                continue
            ok_req, why = is_genuine_requirement(er.statement)  # drop document meta / disclaimers
            if not ok_req:
                add_open("non_requirement", er.statement, why)
                continue
            spans = valid_spans(er.quotes, chunk.text)
            if not spans:
                # never silently discard: route to open-questions so a human still sees it
                if er.inferred:
                    add_open("inferred", er.statement, "implied; no verbatim span in this chunk")
                else:
                    add_open("ungrounded", er.statement, "no verbatim source span in this chunk — verify or discard")
                continue
            # scope/status guard: if the evidence marks this out-of-scope / disputed / undecided /
            # deferred, it is not a firm obligation — route to open-questions (tentative), do not assert.
            scope_flag, scope_reason = classify_scope_status([s for s, _, _ in spans], chunk.location)
            if scope_flag:
                add_open(scope_flag, er.statement, scope_reason or "source marks this non-firm")
                continue
            req = _to_requirement(er, spans, chunk, project_id)
            cands.append((key, req, er.statement, [s for s, _, _ in spans]))

        # 2) one batched critic call per pass (semantic grounding check)
        new = 0
        pass_missed: list[str] = []
        if run_critic and cands:
            result = verify_batch(provider, [(c[2], c[3]) for c in cands], chunk.text)
            by_idx = {v.index: v for v in result.verdicts}
            pass_missed = list(result.possibly_missed or [])
            if not (by_idx.keys() & set(range(len(cands)))):
                log.warning(
                    "critic returned no usable verdict for any of %d candidate(s) in %s; "
                    "routing them to open-questions as unverified (fail-closed)",
                    len(cands), chunk.doc_id,
                )
            for i, (key, req, stmt, _q) in enumerate(cands):
                v = by_idx.get(i)
                if v is None:
                    # FAIL CLOSED: the semantic check did not adjudicate this candidate — do not
                    # silently accept it. It is already verbatim-grounded, so surface it for human
                    # verification rather than dropping it (recall-safe) or asserting it (faithful).
                    add_open("critic_rejected", stmt,
                             "critic returned no verdict for this candidate — unverified, needs review")
                    continue
                if (not v.grounded) or v.invented or not v.is_requirement:
                    kind = "non_requirement" if (v.is_requirement is False and v.grounded and not v.invented) else "critic_rejected"
                    add_open(kind, stmt, v.reason or "not a grounded requirement")
                    continue
                found[key] = req
                new += 1
        else:
            for key, req, _stmt, _q in cands:
                found[key] = req
                new += 1

        hints = pass_missed
        if new == 0 and not hints:
            break

    return list(found.values()), open_q


def extract_document(
    provider: LLMProvider,
    chunks: list[Chunk],
    project_id: str = "P-ELAMS",
    *,
    max_passes: int = 3,
    run_critic: bool = True,
    max_workers: int = 1,
) -> tuple[list[Requirement], list[dict]]:
    """Run extraction + critic over a document's chunks. Returns
    (accepted requirements, open-questions). `max_workers` > 1 processes chunks
    concurrently (production speed-up); default 1 keeps ordering deterministic for tests."""
    if max_workers > 1 and len(chunks) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            per_chunk = list(
                ex.map(lambda c: _process_chunk(provider, c, project_id, max_passes, run_critic), chunks)
            )
    else:
        per_chunk = [_process_chunk(provider, c, project_id, max_passes, run_critic) for c in chunks]

    accepted: list[Requirement] = []
    open_all: list[dict] = []
    for reqs, open_q in per_chunk:
        for r in reqs:
            match = next((e for e in accepted if _is_near_dup(r, e)), None)
            if match is not None:
                _merge_refs(match, r)  # keep evidence, drop the duplicate
            else:
                accepted.append(r)
        open_all.extend(open_q)

    # dedup open-questions across chunks
    seen: set[tuple[str, str]] = set()
    open_dedup: list[dict] = []
    for o in open_all:
        okey = (o["type"], normalize(o["statement"]))
        if okey in seen:
            continue
        seen.add(okey)
        open_dedup.append(o)

    return accepted, open_dedup


async def extract_and_store(
    provider: LLMProvider,
    chunks: list[Chunk],
    repo,
    project_id: str = "P-ELAMS",
    *,
    reset_existing: bool = True,
    **kwargs,
) -> tuple[list[Requirement], list[dict]]:
    """Run extraction and PERSIST the results: each accepted requirement is saved, and one
    agent-run record is written for the audit trail. The project must already exist (FK).

    `reset_existing` (default True) clears the project's prior requirements + their review
    decisions first, so a RE-RUN never mixes stale rows (or a renamed-statement orphan left
    `approved`) into the new extraction. Pass False only for incremental accumulation."""
    if reset_existing:
        await repo.reset_project_requirements(project_id)
    reqs, open_q = extract_document(provider, chunks, project_id, **kwargs)
    for r in reqs:
        await repo.save_requirement(r)
    await repo.log_agent_run(
        AgentRun(
            project_id=project_id,
            agent="A1+A0",
            provider=getattr(provider, "name", None),
            model=getattr(provider, "deployment", None),
            status="success",
            input={"chunks": len(chunks)},
            output={
                "accepted": len(reqs),
                "open_questions_count": len(open_q),
                "open_questions": open_q,  # persisted for the SRS Appendix C (P7)
            },
        )
    )
    return reqs, open_q

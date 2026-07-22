"""Reproducible CLI for the RGA pipeline.

  python -m rga eval    [--split test|dev|all] [--workers N] [--passes N] [--no-critic]
  python -m rga extract --doc brd [--workers N]

Wraps the tested library functions; uses Claude/Foundry with a response cache. Prints a
JSON report including recall/precision, average confidence, and token usage (cost signal).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
from pathlib import Path

from .agents.pipeline import extract_and_store, extract_document
from .config import load_config, load_secrets
from .eval.baseline import go_no_go, load_baseline
from .eval.dataset import load_corpus, normalize
from .eval.scorer import score
from .ingest.pipeline import ingest_corpus
from .llm.cache import CachingProvider
from .llm.factory import get_provider


def _provider(cache_dir: str | None, override: str | None = None):
    cfg = load_config("config.yaml")
    if override and override != "config":
        cfg.provider = override  # CLI override for a live run without editing config.yaml
    prov = get_provider(cfg, load_secrets())
    return CachingProvider(prov, cache_dir) if cache_dir else prov


def _underlying(p):
    return getattr(p, "inner", p)


def _select_chunks(corpus, ing, ids):
    """Chunks that carry a target requirement's quote (bounded, cross-doc)."""
    sel, seen = [], set()
    for r in corpus.requirements:
        if ids is not None and r["id"] not in ids:
            continue
        for s in r["source"]:
            nq = normalize(s["quote"])
            for c in ing[s["doc_id"]]:
                if nq in normalize(c.text) and (c.doc_id, c.index) not in seen:
                    seen.add((c.doc_id, c.index))
                    sel.append(c)
    return sel


def cmd_eval(args) -> None:
    prov = _provider(args.cache, args.provider)
    corpus = load_corpus(args.corpus)
    ing = ingest_corpus(args.corpus)
    ids = None if args.split == "all" else set(getattr(corpus, args.split))
    sel = _select_chunks(corpus, ing, ids)

    reqs, open_q = extract_document(
        prov, sel, project_id="cli", max_passes=args.passes,
        run_critic=not args.no_critic, max_workers=args.workers,
    )
    st = score(reqs, corpus, split_ids=ids)
    sa = score(reqs, corpus, split_ids=None)
    u = _underlying(prov)
    report = {
        "split": args.split,
        "n_chunks": len(sel),
        "n_extracted": st["n_extracted"],
        "recall_explicit": st["recall_explicit"],
        "recall_implicit": st["recall_implicit"],
        "precision_grounded": sa["precision_grounded"],
        "avg_confidence": round(sum(r.confidence for r in reqs) / len(reqs), 3) if reqs else None,
        "low_confidence_lt_0_5": sum(1 for r in reqs if r.confidence < 0.5),
        "open_questions": len(open_q),
        "tokens_in": getattr(u, "tokens_in", None),
        "tokens_out": getattr(u, "tokens_out", None),
        "go_no_go": go_no_go(st["recall_explicit"] or 0.0, None, load_baseline(args.baseline)),
    }
    print(json.dumps(report, indent=2))


def cmd_extract(args) -> None:
    prov = _provider(args.cache, args.provider)
    ing = ingest_corpus(args.corpus)
    if args.doc not in ing:
        raise SystemExit(f"unknown doc '{args.doc}'. Options: {sorted(ing)}")
    reqs, open_q = extract_document(
        prov, ing[args.doc], project_id="cli", max_passes=args.passes,
        run_critic=not args.no_critic, max_workers=args.workers,
    )
    u = _underlying(prov)
    print(json.dumps(
        {
            "doc": args.doc,
            "n_extracted": len(reqs),
            "avg_confidence": round(sum(r.confidence for r in reqs) / len(reqs), 3) if reqs else None,
            "tokens_in": getattr(u, "tokens_in", None),
            "tokens_out": getattr(u, "tokens_out", None),
            "extracted": [
                {"id": r.id, "rtype": r.rtype.value, "statement": r.statement,
                 "confidence": r.confidence, "quotes": [s.raw_quote for s in r.source_refs]}
                for r in reqs
            ],
            "open_questions": open_q,
        },
        indent=2,
        ensure_ascii=False,
    ))


def cmd_store(args) -> None:
    """Extract from the corpus and PERSIST to the configured store, so the review UI has
    something to review. All docs are extracted together (enables cross-doc dedup/merge)."""
    from .models import Project
    from .store.db import Database
    from .store.repository import Repository

    prov = _provider(args.cache, args.provider)
    ing = ingest_corpus(args.corpus)
    docs = [args.doc] if args.doc else list(ing)
    chunks = [c for d in docs for c in ing[d]]

    async def run():
        cfg = load_config("config.yaml")
        db = Database(cfg.store.path)
        await db.init()
        repo = Repository(db)
        await repo.save_project(Project(id=args.project, name=args.project))
        reqs, open_q = await extract_and_store(
            prov, chunks, repo, project_id=args.project,
            max_passes=args.passes, run_critic=not args.no_critic, max_workers=args.workers,
        )
        await db.dispose()
        return reqs, open_q, cfg.store.path

    reqs, open_q, store_path = asyncio.run(run())
    u = _underlying(prov)
    print(json.dumps({
        "project": args.project,
        "store": store_path,
        "stored_requirements": len(reqs),
        "open_questions": len(open_q),
        "avg_confidence": round(sum(r.confidence for r in reqs) / len(reqs), 3) if reqs else None,
        "tokens_in": getattr(u, "tokens_in", None),
        "tokens_out": getattr(u, "tokens_out", None),
    }, indent=2))


def cmd_generate(args) -> None:
    """Generate the Wave-1 handoff pack (SRS + RTM + open-questions + seed models) from the
    APPROVED requirements in the store. Refuses if the review gate is not open."""
    from .generate.handoff import GateNotOpen, generate_handoff
    from .generate.models import seed_models_markdown
    from .generate.open_questions import open_questions_markdown
    from .store.db import Database
    from .store.repository import Repository
    from .util.paths import safe_dir_component

    async def load():
        cfg = load_config("config.yaml")
        db = Database(cfg.store.path)
        await db.init()
        repo = Repository(db)
        reqs = await repo.list_requirements(args.project)
        out = await repo.latest_agent_run_output(args.project)
        await db.dispose()
        return reqs, (out or {}).get("open_questions", [])

    reqs, open_q = asyncio.run(load())
    prov = None if args.no_narrative else _provider(args.cache, args.provider)
    try:
        pack = generate_handoff(
            reqs, project_name=args.project.strip(), date=args.date,
            provider=prov, open_questions=open_q, run_narrative=not args.no_narrative,
        )
    except GateNotOpen as exc:
        raise SystemExit(f"Gate closed — cannot generate: {exc}")

    outdir = Path(args.out or ("handoff/" + safe_dir_component(args.project)))
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "SRS.md").write_text(pack["srs_markdown"], encoding="utf-8")
    (outdir / "RTM.md").write_text(pack["rtm_markdown"], encoding="utf-8")
    (outdir / "open-questions.md").write_text(
        "# Open Questions (Appendix C)\n\n" + open_questions_markdown(pack["open_questions"]),
        encoding="utf-8",
    )
    (outdir / "seed-models.md").write_text(
        "# Seed Models (Appendix B — DRAFT)\n\n" + seed_models_markdown(pack["seed_models"]),
        encoding="utf-8",
    )
    (outdir / "manifest.json").write_text(json.dumps(pack["manifest"], indent=2), encoding="utf-8")
    # also emit Word (.docx) versions of each document (best-effort; never breaks the .md)
    from .generate.docx_export import write_docx_versions

    docx_names = write_docx_versions(outdir, {
        "SRS.md": pack["srs_markdown"],
        "RTM.md": pack["rtm_markdown"],
        "open-questions.md": "# Open Questions (Appendix C)\n\n" + open_questions_markdown(pack["open_questions"]),
        "seed-models.md": "# Seed Models (Appendix B — DRAFT)\n\n" + seed_models_markdown(pack["seed_models"]),
    })
    u = _underlying(prov) if prov else None
    print(json.dumps({
        "out": str(outdir),
        "files": ["SRS.md", "RTM.md", "open-questions.md", "seed-models.md", "manifest.json"] + docx_names,
        **pack["manifest"],
        "tokens_in": getattr(u, "tokens_in", None) if u else None,
        "tokens_out": getattr(u, "tokens_out", None) if u else None,
    }, indent=2))


def cmd_run(args) -> None:
    """Run the whole pipeline as a LangGraph StateGraph. Without --resume it runs up to the
    human-review interrupt and stops; review via `rga serve`, then `rga run --resume`.
    --auto-approve approves everything and runs straight through (demo/CI)."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from .models import ReviewAction
    from .orchestrator.graph import build_graph, compile_graph
    from .review.service import apply_decision
    from .store.db import Database
    from .store.repository import Repository

    async def run():
        cfg = load_config("config.yaml")
        db = Database(cfg.store.path)
        await db.init()
        repo = Repository(db)
        prov = _provider(args.cache, args.provider)
        builder = build_graph(
            prov, repo, run_critic=not args.no_critic, max_passes=args.passes,
            max_workers=args.workers, analyze_llm=not args.no_narrative,
            generate_narrative=not args.no_narrative,
        )
        cp_path = str(Path(cfg.store.path).with_name("checkpoints.sqlite"))
        conf = {"configurable": {"thread_id": args.thread or args.project}}
        async with AsyncSqliteSaver.from_conn_string(cp_path) as cp:
            graph = compile_graph(builder, cp)
            if args.resume:
                final = await graph.ainvoke(None, conf)
            else:
                init = {
                    "project_id": args.project, "project_name": args.project,
                    "corpus_dir": args.corpus, "date": args.date,
                    "out_dir": args.out or f"handoff/{args.project}",
                }
                await graph.ainvoke(init, conf)
                snap = await graph.aget_state(conf)
                paused = snap.next == ("review",)
                if paused and args.auto_approve:
                    # GUARDED auto-approve (same rule as the API): only clean, confident, routine,
                    # non-conflicting, non-inferred requirements are accepted; anything that needs a
                    # human still waits for one (E-M5). --auto-approve no longer waves everything in.
                    from .agents.triage import triage as _triage
                    for r in await repo.list_requirements(args.project):
                        if (_triage(r)["level"] == "routine" and r.confidence >= 0.9
                                and not r.conflicts_with and not r.inferred):
                            await apply_decision(repo, r.id, ReviewAction.accept, actor="auto")
                    final = await graph.ainvoke(None, conf)
                    final["_paused_at_review"] = not final.get("gate_open", False)
                else:
                    final = dict(snap.values)
                    final["_paused_at_review"] = paused
        u = _underlying(prov)
        final["_tokens"] = {"in": getattr(u, "tokens_in", None), "out": getattr(u, "tokens_out", None)}
        await db.dispose()
        return final

    final = asyncio.run(run())
    if final.get("_paused_at_review"):
        print(json.dumps({
            "stage": final.get("stage"),
            "message": "Paused at human review. Approve via `rga serve`, then `rga run --resume`.",
            "n_extracted": final.get("n_extracted"),
            "tokens": final.get("_tokens"),
        }, indent=2))
        return
    print(json.dumps({
        "stage": final.get("stage"),
        "gate_open": final.get("gate_open"),
        "out_dir": final.get("out_dir"),
        "manifest": final.get("manifest"),
        "metrics": final.get("metrics"),
        "tokens": final.get("_tokens"),
    }, indent=2, default=str))


def cmd_serve(args) -> None:
    import uvicorn

    from .api.app import create_app
    from .store.db import Database
    from .store.repository import Repository

    cfg = load_config("config.yaml")
    db = Database(cfg.store.path)
    repo = Repository(db)
    provider = _provider(args.cache, args.provider)  # used when the UI runs the pipeline / generates
    # repo layout: <root>/backend/rga/cli.py and <root>/frontend/dist → three parents up to <root>.
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    app = create_app(repo, db=db, frontend_dir=dist, provider=provider)
    ui = f"http://{args.host}:{args.port}/" if dist.is_dir() else "(frontend not built; API only)"
    print(f"RGA UI on {ui}  |  API: http://{args.host}:{args.port}/api  |  model: {args.provider}  |  store: {cfg.store.path}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def _add_common(sp):
    sp.add_argument("--corpus", default="datasets/elams")
    sp.add_argument("--passes", type=int, default=1)
    sp.add_argument("--workers", type=int, default=6)
    sp.add_argument("--no-critic", action="store_true")
    sp.add_argument("--cache", default=".cache")
    sp.add_argument(
        "--provider", default="config",
        choices=["config", "mock", "foundry", "azure", "claude"],
        help="override config.yaml's provider for this run",
    )


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="rga", description="Agentic Requirement Gathering & Analysis (PoC)")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("eval", help="extract + score against the gold set")
    _add_common(e)
    e.add_argument("--split", default="test", choices=["test", "dev", "all"])
    e.add_argument("--baseline", default="datasets/elams/human_baseline.json")
    e.set_defaults(fn=cmd_eval)

    x = sub.add_parser("extract", help="extract requirements from one document")
    _add_common(x)
    x.add_argument("--doc", required=True)
    x.set_defaults(fn=cmd_extract)

    s = sub.add_parser("store", help="extract + persist to the store (populate the review UI)")
    _add_common(s)
    s.add_argument("--project", default="P-ELAMS")
    s.add_argument("--doc", default=None, help="one doc id; default = all docs")
    s.set_defaults(fn=cmd_store)

    g = sub.add_parser("generate", help="generate the handoff pack (SRS+RTM+…) from approved reqs")
    g.add_argument("--project", default="P-ELAMS")
    g.add_argument("--out", default=None, help="output dir (default handoff/<project>)")
    g.add_argument("--date", default=datetime.date.today().isoformat())
    g.add_argument("--no-narrative", action="store_true", help="skip the LLM prose (all narrative -> TBD)")
    g.add_argument("--provider", default="config", choices=["config", "mock", "foundry", "azure", "claude"])
    g.add_argument("--cache", default=".cache")
    g.set_defaults(fn=cmd_generate)

    r = sub.add_parser("run", help="run the full pipeline (LangGraph) with a human-review interrupt")
    _add_common(r)
    r.add_argument("--project", default="P-ELAMS")
    r.add_argument("--out", default=None)
    r.add_argument("--date", default=datetime.date.today().isoformat())
    r.add_argument("--thread", default=None, help="checkpoint thread id (default = project)")
    r.add_argument("--resume", action="store_true", help="resume after human review")
    r.add_argument("--auto-approve", action="store_true", help="approve everything and run straight through")
    r.add_argument("--no-narrative", action="store_true", help="skip LLM prose + ambiguity explanations")
    r.set_defaults(fn=cmd_run)

    v = sub.add_parser("serve", help="run the full UI (input → run → review → generate) over the store")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    v.add_argument("--provider", default="config", choices=["config", "mock", "foundry", "azure", "claude"],
                   help="LLM used when the UI runs the pipeline / generates (default: config.yaml)")
    v.add_argument("--cache", default=".cache")
    v.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

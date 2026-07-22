# RGA PoC — Agentic Requirement Gathering & Analysis

An agentic pipeline that turns raw project inputs (BRDs, discovery-call transcripts, emails,
survey CSVs, Jira exports) into a **human-reviewed, evidence-traceable Requirements→Design
handoff pack** (IEEE-830 SRS + RTM + open questions + seed models). Every generated requirement
traces back to a byte-accurate source span, and nothing reaches the generators until a human
approves it at a hard review gate.

Phase-1 (Wave-1 "thin slice") proof-of-concept, built one development phase at a time.

## Current status: **P0–P8 complete + a system-wide hardening pass** (full pytest + frontend suites green)

Provider in use: **Claude Sonnet 4-6 on Azure AI Foundry** (`provider: foundry`); default is `mock` so the suite runs with **no credentials**.

| Phase | Delivers |
|---|---|
| **P0** Foundations | data model; `LLMProvider` (mock/foundry/azure/claude) with retry + backoff + timeout + never-unvalidated `structured()`; SQLite (WAL) store; bounded-loop helper |
| **P1** Dataset | verified universal eval corpus (`datasets/elams/`, 41 reqs) + loader/validators |
| **P2** Ingestion | per-modality loaders + deterministic Source-ID + structure-aware chunker (byte-accurate slices) |
| **P3** Extraction | A1 extraction + A0 critic + **deterministic byte-accurate grounding** + open-questions; near-dedup; stable content-hash IDs |
| **P4** Evaluation | span-overlap scorer (recall/precision/per-difficulty) + human-baseline go/no-go bar |
| **P5** Ambiguity | QuARS lexicon + EARS conformance (deterministic) → LLM explanation + EARS rewrite |
| **P6** Review gate | FastAPI review API + minimal React (Vite/TS/TanStack Query) table; accept/edit/reject with before/after decision log; **hard gate — only `approved` reach the generators**; confidence recorded, not routed |
| **P7** Generators | G1 SRS (IEEE-830/Wiegers, full reference structure, hybrid LLM narrative else `[TBD]`) + G2 RTM (deterministic, evidence-carrying) + open-questions (Appendix C) + bounded DRAFT seed models (Mermaid use-case + ERD); gate-enforced handoff pack |
| **P8** Orchestration | **LangGraph StateGraph** (ingest→extract→analyze→rules→review→generate→metrics→baseline) with `interrupt_before` at the human gate + **resumable checkpointer** (survives a kill); exit-metrics report (recall vs bar, ambiguity, accept/edit rate, time-saved); Wave-1 handoff pack + manifest |
| Hardening | request-timeout on clients, response caching, bounded concurrency, persistence+audit wiring, chunker robustness, batched critic, token/cost accounting, CLI |

Live results (sealed test split): explicit recall **0.944**, implicit **1.0**; precision improving via near-dedup.

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** and npm (only for the review UI in P6; the Python suite runs without it)
- No API credentials required to run the tests or the pipeline — the default provider is `mock`.
  A real run against Claude needs an Azure AI Foundry key (see [Using a real provider](#using-a-real-provider)).

## Setup

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate     |  macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

# copy the env template — leave the keys blank to stay on the mock provider
cp .env.example .env        # Windows: copy .env.example .env
```

## Run the test suite (the P0 acceptance gate)

```bash
python -m pytest
```

| Test file | Covers | Goal |
|---|---|---|
| `tests/test_tc0_1_provider_factory.py` | TC0.1 | provider chosen by **config only**, zero pipeline-code change |
| `tests/test_tc0_2_structured_output.py` | TC0.2 | valid model returned; malformed reply repaired; **no unvalidated output escapes** |
| `tests/test_tc0_3_store_roundtrip_concurrency.py` | TC0.3 | round-trip intact; concurrent writes — **no "database is locked"** |
| `tests/test_tc0_4_resilience.py` | TC0.4 | transient errors retried w/ backoff; **hard-stop after N**; timeout fires on a hang |

## CLI — run the pipeline reproducibly

```bash
# extract from one document (default provider comes from config.yaml = mock)
python -m rga extract --doc brd

# drive a live run against Claude/Foundry, 4 chunks in parallel, no critic
python -m rga extract --doc survey --provider foundry --workers 4 --no-critic

# extract + score against the sealed test split; prints recall/precision, avg
# confidence, token usage (cost signal), and the go/no-go verdict
python -m rga eval --split test --provider foundry
```

Every report includes `avg_confidence` / `low_confidence_lt_0_5` (which requirements to review
first) and `tokens_in` / `tokens_out` (per-run cost). A warm `--cache` dir replays prior
responses, so re-runs cost **0 tokens** and are byte-for-byte reproducible.

## Human review UI (P6)

The review gate is where a human accepts / edits / rejects every candidate requirement.
Nothing reaches the SRS/RTM generators until it is `approved`, and every decision is logged
(before/after + actor + timestamp). Confidence is shown but **never** used to gate.

```bash
# 1. populate the store (extract + persist a corpus into config.yaml's store.path)
python -m rga store --provider foundry --project P-ELAMS      # or --doc survey for a quick subset

# 2. build the frontend once, then run the API + UI on one origin
cd frontend && npm install && npm run build && cd ..
python -m rga serve                                           # http://127.0.0.1:8000

# frontend dev mode (hot reload; proxies /api to :8000): cd frontend && npm run dev
```

Review API (all under `/api`): `GET projects/{pid}/requirements` · `GET projects/{pid}/gate`
· `POST projects/{pid}/generate` (409 until the gate is open; returns approved-only) ·
`GET requirements/{rid}` (with decision log) · `POST requirements/{rid}/review` (accept/edit/reject).
The gate rules (`rga/review/gate.py`) are pure and unit-tested; the generators (P7) consume
`approved_only(...)`, so a rejected or un-reviewed item can never enter an SRS/RTM.

## Generate the handoff pack (P7)

Once requirements are approved (P6 gate open), generate the Wave-1 Requirements→Design
handoff. Generation is **refused unless the gate is open**, and only approved requirements
are ever rendered.

```bash
python -m rga generate --project P-ELAMS --provider foundry     # SRS narrative via LLM
python -m rga generate --project P-ELAMS --no-narrative         # deterministic only (prose -> [TBD])
```

Writes to `handoff/<project>/`: `SRS.md` (IEEE-830/Wiegers — every reference section present),
`RTM.md` (each approved requirement ↔ SRS id ↔ SRS section ↔ source evidence),
`open-questions.md` (Appendix C), `seed-models.md` (Appendix B — DRAFT Mermaid use-case + ERD),
and `manifest.json`. The SRS/RTM share one id map, so every SRS line traces to a source and back.

**Honest boundaries (Wave-1):** §4 groups by requirement `feature`. Consolidation, scope
reconciliation, conflict detection, adversarial verification, and completeness/coverage
analysis now run in a single shared analysis phase (`rga.agents.analysis.run_analysis`) — the
SAME for the CLI graph (`rga run`) and the UI, so they cannot diverge. Seed analysis models
remain *draft*; refined design models are a later (Design) phase, as the manifest states.

## End-to-end run (P8 — LangGraph, human-gated, resumable)

```bash
# run the whole pipeline; it pauses at the human-review interrupt
python -m rga run --project P-ELAMS --corpus datasets/elams --provider foundry
#   → extracts + analyses, then STOPS at AWAITING_REVIEW (state checkpointed)

python -m rga serve            # review in the UI: accept / edit / reject
python -m rga run --project P-ELAMS --resume --provider foundry
#   → resumes from the checkpoint → generates SRS/RTM → metrics → BASELINED

# demo / CI: approve everything and run straight through
python -m rga run --project P-ELAMS --corpus datasets/elams --provider foundry --auto-approve
```

The orchestrator is a LangGraph `StateGraph` compiled with `interrupt_before=["review"]`
and an `AsyncSqliteSaver` checkpointer (`data/checkpoints.sqlite`). Because the checkpoint
persists, a killed run resumes from where it paused — the review gate is a natural,
durable pause point. The final state carries the **exit-metrics report** (recall vs the
data-driven human bar, ambiguity coverage, acceptance/edit/reject rates, time-saved) and
the handoff **manifest**. The formal go/no-go reads `pending_human_baseline` until the BA
baseline session is recorded.

## Using a real provider
1. Put `ANTHROPIC_FOUNDRY_API_KEY` in `.env` and the endpoint/deployment in `config.yaml` (`foundry:`).
2. Set `provider: foundry` in `config.yaml`, **or** pass `--provider foundry` on the CLI.

An Azure OpenAI alternative (`provider: azure`, `AZURE_OPENAI_*` in `.env`) is also wired.
No pipeline-code changes either way — that's the point of the abstraction.

## Repository layout

```
rga/                 Python package — the pipeline
  llm/               provider abstraction (mock/foundry/azure/claude) + retry/cache/factory
  ingest/            per-modality loaders, deterministic Source-IDs, structure-aware chunker
  agents/            extraction, critic, grounding, analysis (consolidate/conflict/coverage/…)
  rules/             QuARS ambiguity lexicon + EARS conformance
  review/            human-gate rules + review service (pure, unit-tested)
  generate/          SRS, RTM, open-questions, seed models, .docx export
  eval/              span-overlap scorer, dataset loader, human baseline
  orchestrator/      LangGraph StateGraph + exit-metrics
  api/               FastAPI review API
  cli.py             `python -m rga …` entry point
frontend/            Vite + React + TypeScript review UI (P6)
datasets/            synthetic eval corpora (ELAMS + others) — committed
templates/           SRS/RTM document templates
tests/               pytest acceptance + unit suites
config.yaml          which provider + all tunables (no secrets)
.env.example         env template — copy to .env, never commit .env
```

Generated and local-only artifacts — `data/` (SQLite store + uploads), `handoff/` (generated
packs), `.cache/` (LLM response cache), `.venv/`, and `node_modules/` — are git-ignored and
recreated on demand.

## Notes

- **Secrets never enter git.** `.env` is git-ignored; only `.env.example` (blank keys) is committed.
- This is an internal Zensar proof-of-concept. Treat the synthetic datasets as illustrative, not production data.

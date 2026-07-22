# RGA PoC — Agentic Requirement Gathering & Analysis

An agentic pipeline that turns raw project inputs (BRDs, discovery-call transcripts, emails,
survey CSVs, Jira exports) into a **human-reviewed, evidence-traceable Requirements→Design
handoff pack** (IEEE-830 SRS + RTM + open questions). Every generated requirement traces back to a
byte-accurate source span, and nothing reaches the generators until a human approves it at a hard
review gate.

The codebase is split into two independent apps that run with **separate commands**:

- **`backend/`** — the Python pipeline + FastAPI API (extraction, grounding, analysis, review gate, SRS/RTM generation, LangGraph orchestration).
- **`frontend/`** — the Vite + React + TypeScript review UI.

The backend exposes a JSON API under `/api`; the frontend talks to it. In production the backend can
also serve the compiled frontend at `/`, so a single process hosts the whole app.

---

## Folder structure

```
rga-code/                     repository root (git)
├── backend/                  ── THE BACKEND (Python) — run everything below from here ──
│   ├── rga/                  Python package — the pipeline
│   │   ├── llm/              provider abstraction (mock/foundry/azure/claude) + retry/cache/factory
│   │   ├── ingest/           per-modality loaders, deterministic Source-IDs, structure-aware chunker
│   │   ├── agents/           extraction, critic, grounding, analysis (consolidate/conflict/coverage/…)
│   │   ├── rules/            QuARS ambiguity lexicon + EARS conformance
│   │   ├── review/           human-gate rules + review service (pure, unit-tested)
│   │   ├── generate/         SRS, RTM, open-questions, design tokens, glossary, .docx export
│   │   ├── eval/             span-overlap scorer, dataset loader, human baseline
│   │   ├── orchestrator/     LangGraph StateGraph + exit-metrics
│   │   ├── store/            SQLite (WAL) database + repository
│   │   ├── api/app.py        FastAPI review/generate API (served under /api)
│   │   ├── cli.py            `python -m rga …` entry point (eval / extract / store / run / generate / serve)
│   │   └── config.py         config + secrets loading
│   ├── tests/                pytest acceptance + unit suites (293 tests)
│   ├── datasets/             synthetic input corpora (elams, e-commerce, Horizon-Green) — committed
│   ├── templates/            SRS/RTM document templates
│   ├── config.yaml           which provider + all tunables (no secrets)
│   ├── requirements.txt       Python dependencies
│   ├── pyproject.toml         package + pytest config
│   ├── .env.example          env template — copy to backend/.env (never committed)
│   ├── data/                 (generated) SQLite store + uploaded docs — git-ignored
│   └── handoff/              (generated) SRS/RTM output packs per project — git-ignored
│
├── frontend/                 ── THE FRONTEND (Vite + React + TypeScript) — run from here ──
│   ├── src/                  React app (App.tsx, api.ts, styles.css, tests)
│   ├── index.html            Vite entry
│   ├── package.json          npm scripts: dev / build / preview / test
│   ├── vite.config.ts        dev server proxies /api → http://127.0.0.1:8000
│   └── dist/                 (generated) production build the backend serves at / — git-ignored
│
├── README.md
└── .gitignore
```

`backend/.venv/` and `frontend/node_modules/` are local, git-ignored, and recreated by the setup
steps below.

---

## How it works (phases)

Provider in use: **Claude Sonnet on Azure AI Foundry** (`provider: foundry`); default is `mock` so
the test suite runs with **no credentials**.

| Phase | Delivers |
|---|---|
| **P0** Foundations | data model; `LLMProvider` (mock/foundry/azure/claude) with retry + backoff + timeout + never-unvalidated `structured()`; SQLite (WAL) store |
| **P1** Dataset | verified eval corpus (`backend/datasets/elams/`) + loader/validators |
| **P2** Ingestion | per-modality loaders + deterministic Source-ID + structure-aware chunker (byte-accurate slices) |
| **P3** Extraction | A1 extraction + A0 critic + **deterministic byte-accurate grounding** + open-questions; near-dedup; stable content-hash IDs |
| **P4** Evaluation | span-overlap scorer (recall/precision) + human-baseline go/no-go bar |
| **P5** Ambiguity | QuARS lexicon + EARS conformance → LLM explanation + EARS rewrite |
| **P6** Review gate | FastAPI review API + React review UI; accept/edit/reject with before/after decision log; **hard gate — only `approved` reach the generators** |
| **P7** Generators | IEEE-830 SRS (title page, ToC, §1–§7, appendices) + RTM (evidence-carrying) + Appendix C; per-aspect technology-stack review; gate-enforced handoff pack |
| **P8** Orchestration | **LangGraph StateGraph** (ingest→extract→analyze→rules→review→generate→metrics) with `interrupt_before` at the human gate + **resumable checkpointer**; exit-metrics report + manifest |

---

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** and npm
- No API credentials required to run the tests or the pipeline — the default provider is `mock`.
  A real run against Claude needs an Azure AI Foundry key (see [Using a real provider](#using-a-real-provider)).

---

## Setup

The backend and the frontend are set up independently.

### 1. Backend

```bash
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate     |  macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

# copy the env template — leave the keys blank to stay on the mock provider
cp .env.example .env          # Windows: copy .env.example .env
```

### 2. Frontend

```bash
cd frontend
npm install
```

---

## Running the app (two separate commands)

The backend and frontend run as **separate processes**, each with its own command. Use two terminals.

### Terminal 1 — backend (API on :8000)

```bash
cd backend
python -m rga serve                      # http://127.0.0.1:8000  (API under /api)
```

### Terminal 2 — frontend

**Development (hot reload):**

```bash
cd frontend
npm run dev                              # http://127.0.0.1:5173  (proxies /api → :8000)
```
Open **http://127.0.0.1:5173**. Vite proxies all `/api` calls to the backend on :8000, so both run
cross-origin-free.

**Production (single origin):** build the frontend once; the backend then serves it at `/`.

```bash
cd frontend && npm run build             # writes frontend/dist/
cd ../backend && python -m rga serve     # open http://127.0.0.1:8000  (UI + API on one origin)
```

In the UI: create/select a project → upload inputs (or pick a dataset) → **run** the pipeline →
**review** (accept/edit/reject) at the gate → choose the technology stack → **generate** the SRS/RTM
handoff pack.

---

## CLI (backend) — run the pipeline reproducibly

All CLI commands run from `backend/` (with the venv active):

```bash
cd backend

# extract from one document (default provider from config.yaml = mock)
python -m rga extract --doc brd

# extract + score against the sealed test split (recall/precision, tokens, go/no-go)
python -m rga eval --split test --provider foundry

# populate the store from a corpus, so the review UI has something to review
python -m rga store --provider foundry --project P-ELAMS

# generate the handoff pack from APPROVED requirements (refused unless the gate is open)
python -m rga generate --project P-ELAMS --provider foundry     # SRS narrative via LLM
python -m rga generate --project P-ELAMS --no-narrative         # deterministic only

# full pipeline (LangGraph) — pauses at the human-review interrupt, then resume after review
python -m rga run --project P-ELAMS --corpus datasets/elams --provider foundry
python -m rga run --project P-ELAMS --resume --provider foundry
```

Handoff packs are written to `backend/handoff/<project>/` (`SRS.md`/`.docx`, `RTM.md`/`.docx`,
`manifest.json`). A warm `--cache` dir replays prior LLM responses, so re-runs cost 0 tokens and are
reproducible.

---

## Run the test suites

```bash
# backend (293 tests)
cd backend && python -m pytest

# frontend (9 tests)
cd frontend && npm test
```

---

## Using a real provider

1. Put `ANTHROPIC_FOUNDRY_API_KEY` in `backend/.env` and the endpoint/deployment in
   `backend/config.yaml` (`foundry:`).
2. Set `provider: foundry` in `backend/config.yaml`, **or** pass `--provider foundry` on the CLI.

An Azure OpenAI alternative (`provider: azure`, `AZURE_OPENAI_*` in `.env`) is also wired. No
pipeline-code changes either way — that's the point of the provider abstraction.

---

## Notes

- **Secrets never enter git.** `backend/.env` is git-ignored; only `backend/.env.example` (blank keys) is committed.
- Generated/local-only artifacts — `backend/data/`, `backend/handoff/`, `backend/.cache/`,
  `backend/.venv/`, and `frontend/node_modules/` / `frontend/dist/` — are git-ignored and recreated on demand.
- This is an internal Zensar proof-of-concept. Treat the synthetic datasets as illustrative, not production data.

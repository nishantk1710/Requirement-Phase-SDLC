![1787121366842](image/README/1787121366842.png)![1787121377548](image/README/1787121377548.png)![1787121381298](image/README/1787121381298.png) Recondensation and along the left disturbance meet home pulling last school grammar and wid bottle medicine problems screenare places forit came shit scratch to open target schools tracker URLet of human browser and shut session session cap to# RGA — Agentic Requirement Gathering & Analysis

RGA is an agentic pipeline that turns raw, messy project inputs (BRDs, discovery-call transcripts,
email threads, product backlogs, ops intake forms) into a **human-reviewed, evidence-traceable
Requirements → Design handoff pack**: an IEEE-830 **SRS**, a **Requirements Traceability Matrix
(RTM)**, and an open-questions appendix. Every requirement traces back to a byte-accurate source
quote, and nothing reaches the document generators until a human approves it at a hard review gate.

This document is written for a review team: it describes the **system**, the **folder structure**,
the **inputs and outputs**, the **end-to-end flow**, **every API endpoint and its role**, and the
**logs** the system emits.

> **Integrating RGA into the combined SDLC frontend?** See **[`INTEGRATION.md`](INTEGRATION.md)** —
> the dedicated contract for the unified UI: full flow, inputs/outputs, **every API endpoint** with
> request/response shapes, and **exactly which progress signals/logs to surface** (and which to
> keep server‑side). The sections below cover the same ground for the review team.

The codebase is split into two apps that run with **separate commands**:

- **`backend/`** — Python pipeline + FastAPI API (extraction, grounding, analysis, review gate, SRS/RTM generation, LangGraph orchestration).
- **`frontend/`** — Vite + React + TypeScript review UI.

---

## 1. Folder structure

```
rga-code/                     repository root (git)
├── backend/                  ── BACKEND (Python) — run all python/pytest commands from here ──
│   ├── rga/                  the pipeline package
│   │   ├── llm/              provider abstraction (mock / foundry / azure / claude) + retry + response cache
│   │   ├── ingest/           per-modality loaders, deterministic Source-IDs, structure-aware chunker
│   │   ├── agents/           extraction (A1), critic (A0), grounding, analysis (consolidate/conflict/
│   │   │                     coverage/completeness/verify), triage, prioritise, narrative, tech-stack
│   │   ├── rules/            QuARS ambiguity lexicon + EARS conformance (deterministic)
│   │   ├── review/           human-gate rules + review service (pure, unit-tested)
│   │   ├── generate/         SRS, RTM, open-questions, design tokens, glossary, .docx export
│   │   ├── eval/             span-overlap scorer, dataset loader, human baseline
│   │   ├── orchestrator/     LangGraph StateGraph + exit-metrics
│   │   ├── store/            SQLite (WAL) database + repository
│   │   ├── api/app.py        FastAPI app — all HTTP endpoints (served under /api)
│   │   ├── cli.py            `python -m rga …` entry point (eval / extract / store / run / generate / serve)
│   │   ├── config.py         config + secrets loading
│   │   └── logging_setup.py  log format + logger factory
│   ├── tests/                pytest acceptance + unit suites (293 tests)
│   ├── datasets/             synthetic input corpora (elams, e-commerce, Horizon-Green) — committed
│   ├── templates/            SRS/RTM document templates
│   ├── config.yaml           provider choice + all tunables (no secrets)
│   ├── requirements.txt      Python dependencies
│   ├── pyproject.toml        package + pytest config
│   ├── .env.example          env template — copy to backend/.env (never committed)
│   ├── data/                 (generated, git-ignored) SQLite store `rga.db`, checkpoints, uploaded docs
│   └── handoff/              (generated, git-ignored) SRS/RTM output packs per project
│
├── frontend/                 ── FRONTEND (Vite + React + TS) — run all npm commands from here ──
│   ├── src/App.tsx           the workspace UI (Input → Run → Review → SRS)
│   ├── src/Landing.tsx       landing page
│   ├── src/api.ts            typed client for the backend API
│   ├── src/styles.css        styling
│   ├── package.json          npm scripts: dev / build / preview / test
│   ├── vite.config.ts        dev server proxies /api → http://127.0.0.1:8000
│   └── dist/                 (generated, git-ignored) production build served by the backend at /
│
├── README.md
└── .gitignore
```

---

## 2. Inputs and Outputs

### Inputs
Raw requirement-bearing documents, in any mix of these formats:

| Modality | Formats | Example |
|---|---|---|
| Business requirements doc (BRD) | `.docx`, `.md` | scope, objectives, business rules |
| Discovery / call transcript | `.txt` | stakeholder conversation |
| Email thread | `.txt` | payments/tax/returns discussion |
| Product backlog | `.csv` | Jira-style epics + stories |
| Ops / intake form | `.docx` | shipping, returns, stock rules |
| Jira export | `.json` | issue objects |

Inputs enter the system one of two ways:
- **Upload** through the UI (or `POST /api/projects/{pid}/upload`) → copied to `backend/data/uploads/<project>/docs/`.
- **Prepared datasets** under `backend/datasets/` (elams, e-commerce, Horizon-Green), selectable in the UI.

### Outputs
A per-project handoff pack written to `backend/handoff/<project>/` (and viewable/downloadable in the UI):

| File | What it is |
|---|---|
| `SRS.md` / `SRS.docx` | IEEE-830 Software Requirements Specification — title page, Table of Contents, §1–§7, Appendices A (glossary) / B (analysis-model deferral) / C (open questions). The `.docx` has a centered title page and a live, page-numbered Table of Contents. |
| `RTM.md` / `RTM.docx` | Requirements Traceability Matrix — each approved requirement ↔ SRS id ↔ SRS section ↔ source document(s) ↔ verbatim evidence quote. |
| `manifest.json` | Counts (functional / non-functional / business), approved total, open-questions count, traceability-complete flag, tech-stack summary. |

Every requirement in the SRS carries a formal id (REQ-/NFR-/BR-n); the RTM proves each one traces
to a byte-accurate source quote. Requirements that could not be grounded or that need a human
decision surface in **Appendix C**, never silently dropped.

---

## 3. End-to-end system flow

The UI has four phases; each maps to backend endpoints. The pipeline itself is a sequence of agents.

```
 ┌── Phase 1 · INPUT ─────────────────────────────────────────────────────────┐
 │ Pick a dataset or upload docs.                                             │
 │ APIs: GET /api/config, GET /api/corpora, POST /api/projects/{pid}/upload   │
 └────────────────────────────────────────────────────────────────────────────┘
                                   │
 ┌── Phase 2 · RUN AGENTS ─────────▼──────────────────────────────────────────┐
 │ POST /api/projects/{pid}/run  → runs the pipeline in the background:        │
 │   ingest → chunk → EXTRACT (A1) → GROUND (byte-accurate) → CRITIC (A0)      │
 │   → dedup → consolidate → reconcile scope → detect conflicts               │
 │   → verify (second opinion) → completeness → coverage floor → analyze       │
 │     (clarity/EARS, priority, owner-routing, triage, guarded auto-approve)   │
 │   → persist requirements + one agent-run record.                            │
 │ Poll: GET /api/projects/{pid}/status  (stage, state, message)              │
 └────────────────────────────────────────────────────────────────────────────┘
                                   │
 ┌── Phase 3 · REVIEW (hard gate) ─▼──────────────────────────────────────────┐
 │ Review by DECISION (clustered, owner-routed) + a technology-stack picker.  │
 │ Read:   GET /requirements, GET /gate, GET /decisions, GET /tech-stack,      │
 │         GET /requirements/{rid}, GET /spot-check, GET /calibration          │
 │ Act:    POST /requirements/{rid}/review, /auto-accept, /accept-all,         │
 │         /review-bulk, /requirements (add), /decisions/apply-recommended,    │
 │         /decisions/{id}/resolve, /tech-stack/select                         │
 │ Gate opens only when every requirement is triaged and ≥1 is approved.      │
 └────────────────────────────────────────────────────────────────────────────┘
                                   │
 ┌── Phase 4 · GENERATE ───────────▼──────────────────────────────────────────┐
 │ POST /api/projects/{pid}/generate → builds SRS + RTM + manifest (+ .docx)  │
 │   in the background from APPROVED requirements only (refused if gate shut). │
 │ Poll:  GET /api/projects/{pid}/generate-status                             │
 │ Fetch: GET /api/projects/{pid}/artifacts, /artifacts/{name}                │
 └────────────────────────────────────────────────────────────────────────────┘

 Admin (any time): DELETE /api/projects/{pid} (reset one project) · POST /api/reset (wipe all)
```

**The hard gate** (`rga/review/gate.py`) is the safety property: the generators consume only
`approved` requirements, so a rejected or un-reviewed item can never enter an SRS or RTM.

---

## 4. API reference

All endpoints are under `/api`. The API is created in `backend/rga/api/app.py`. Grouped by role:

### System & configuration
| Method | Path | Role |
|---|---|---|
| `GET` | `/api/health` | Liveness check. |
| `GET` | `/api/config` | Provider/model in use + whether it's the credential-free `mock` (drives UI warnings). |
| `GET` | `/api/corpora` | Lists selectable document sets — prepared `datasets/` + prior uploads. |

### Input
| Method | Path | Role |
|---|---|---|
| `POST` | `/api/projects/{pid}/upload` | Upload input documents (multipart) → stored under `data/uploads/{pid}/docs/`. |

### Pipeline run
| Method | Path | Role |
|---|---|---|
| `POST` | `/api/projects/{pid}/run` | Start the full extract + analyze pipeline in the **background**. |
| `GET` | `/api/projects/{pid}/status` | Run progress: current `stage`, `state` (running/done/error), and a message. |

### Requirements & the review gate
| Method | Path | Role |
|---|---|---|
| `GET` | `/api/projects/{pid}/requirements` | List requirements (`include=all\|pending\|approved`). |
| `GET` | `/api/projects/{pid}/gate` | Is the gate open? (all triaged, ≥1 approved) + the reason if not. |
| `GET` | `/api/requirements/{rid}` | One requirement with its full before/after decision log. |
| `POST` | `/api/requirements/{rid}/review` | Accept / edit / reject one requirement (logged with actor + timestamp). |
| `POST` | `/api/projects/{pid}/auto-accept` | Review-by-exception: auto-approve only the safe, high-confidence, unflagged, non-conflicting, non-inferred "routine" candidates. |
| `POST` | `/api/projects/{pid}/accept-all` | Approve every pending requirement at once (each logged). |
| `POST` | `/api/projects/{pid}/review-bulk` | Apply one decision (accept/reject) to many requirements at once. |
| `POST` | `/api/projects/{pid}/requirements` | Add a human-authored requirement (e.g. from a gap / possible-miss decision). |

### Decisions (clustered review)
| Method | Path | Role |
|---|---|---|
| `GET` | `/api/projects/{pid}/decisions` | Review by *decision*, not by row: clustered, owner-routed items, each with a recommendation. |
| `POST` | `/api/projects/{pid}/decisions/apply-recommended` | Apply every still-open decision's recommended verdict in one action. |
| `POST` | `/api/projects/{pid}/decisions/{decision_id}/resolve` | Persist that one decision is resolved (applying its requirement mutations). |

### Technology stack (SRS §7)
| Method | Path | Role |
|---|---|---|
| `GET` | `/api/projects/{pid}/tech-stack` | Per-aspect stack candidates (adopted-from-inputs or proposed, one recommended each). |
| `POST` | `/api/projects/{pid}/tech-stack/select` | Record the reviewer's chosen candidate for one aspect (or a custom "Other" value). |

### QA / calibration
| Method | Path | Role |
|---|---|---|
| `GET` | `/api/projects/{pid}/spot-check` | Deterministic sample (~`rate`) of the auto-approved set for a human QA glance. |
| `GET` | `/api/projects/{pid}/calibration` | Human acceptance rate per confidence band + a suggested auto-approve bar. |

### Generation & artifacts
| Method | Path | Role |
|---|---|---|
| `POST` | `/api/projects/{pid}/generate` | Start SRS/RTM/manifest (+ .docx) generation in the **background**. Refused unless the gate is open. |
| `GET` | `/api/projects/{pid}/generate-status` | Generation progress/state. |
| `GET` | `/api/projects/{pid}/artifacts` | List generated artifact filenames for the project. |
| `GET` | `/api/projects/{pid}/artifacts/{name}` | Fetch one artifact (`SRS.md`, `RTM.md`, or the `.docx`) for preview/download. |

### Admin (destructive)
| Method | Path | Role |
|---|---|---|
| `DELETE` | `/api/projects/{pid}` | Clear all data for one project (requirements, chunks, decisions, runs). Uploaded files survive. |
| `POST` | `/api/reset` | Wipe the entire store (all projects) and recreate an empty schema. |

The built frontend is mounted at `/` (all `/api/*` routes take priority).

---

## 5. Logs

Logging is configured in `backend/rga/logging_setup.py`. Every line uses:

```
%(asctime)s %(levelname)-7s %(name)-14s | %(message)s
# e.g.  12:40:59 INFO    rga.api        | run[Commerce App] extracted 84 candidate requirement(s)
```

Logs stream to the console of the process running `python -m rga serve` (or the CLI). Loggers:

| Logger | Emitted from | What it reports |
|---|---|---|
| `rga.config` | `config.py` | `config loaded from … (provider=…, foundry.deployment=…)`; warns when `config.yaml` is missing (falls back to `mock`). |
| `rga.api` | `api/app.py` | Pipeline & generation lifecycle: `run[pid] <stage> — <msg>`, `run[pid] extracted N candidate requirement(s)`, `run[pid] analysis: N canonical, C conflict(s), coverage X%`, `run[pid] LLM cache this run: H hits, M misses`, `run[pid] DONE — …`; `generate[pid] started (N approved)`, `generate[pid] DONE — wrote … to …`; errors. |
| `rga.extraction` | `agents/pipeline.py` | Per-pass extraction convergence: `convergence pass N: +K new (total T)`; critic fail-closed warnings. |
| `rga.llm` | `llm/base.py` | `LLM call failed after N attempts`; `structured() truncated at max_tokens=…; retrying at …`. |
| `rga.narrative` | `agents/narrative.py` | `narrative drafting failed (…); prose sections will render as TBD` (best-effort fallback). |
| `rga.loop` | `util/loop.py` | Bounded convergence-loop progress. |

What to watch during a run: `rga.api` prints the stage transitions and the final `DONE` summary
(requirement counts, conflicts, coverage). A stalled or failed run shows the failing stage there;
LLM/provider problems show under `rga.llm`.

---

## 6. Setup

Backend and frontend are set up independently.

**Backend**
```bash
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate     |  macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # Windows: copy .env.example .env   (leave keys blank to stay on mock)
```

**Frontend**
```bash
cd frontend
npm install
```

**Prerequisites:** Python 3.11+, Node.js 18+. No API credentials are needed for the tests or a mock
run; a real extraction run needs an Azure AI Foundry key (see §9).

---

## 7. Running the app (two separate commands)

Use two terminals.

| Terminal | Command | Result |
|---|---|---|
| **Backend** | `cd backend && python -m rga serve` | API + pipeline on `http://127.0.0.1:8000` |
| **Frontend (dev)** | `cd frontend && npm run dev` | UI on `http://127.0.0.1:5173`, proxies `/api` → :8000 |
| **Frontend (prod)** | `cd frontend && npm run build` | writes `frontend/dist/`; the backend then serves the UI at `/` |

In the UI: **1 · Input** (pick/upload docs) → **2 · Run agents** → **3 · Review** (approve decisions +
pick the tech stack) → **4 · SRS** (generate + download the SRS/RTM).

---

## 8. CLI (backend, from `backend/`)

```bash
python -m rga serve                                     # run the API + UI
python -m rga run --project P1 --corpus datasets/elams --provider foundry   # full pipeline (pauses at review)
python -m rga run --project P1 --resume --provider foundry                  # resume after review → generate
python -m rga store --project P1 --provider foundry     # extract + persist (populate the review UI)
python -m rga generate --project P1 --provider foundry  # build the handoff pack from approved reqs
python -m rga eval --split test --provider foundry       # extract + score against the gold set
python -m rga validate-srs handoff/P1/SRS.docx           # check an SRS against the Design-parser format schema
```

The **format validator** (`validate-srs`) checks a generated SRS (`.docx` or `.md`) against the
reference schema in `backend/rga/generate/srs_format_schema.json` — the contract the Design team's
parser depends on (required sections, the §2.3/§3.3/Appendix‑A tables, `REQ-/NFR-/BR-n` tags, the
Appendix‑B entities line). It exits non‑zero on any violation. Every `generate` also runs this check
automatically and records the result in the pack's `manifest.json` under `format_validation`.

`python -m rga run` uses a LangGraph StateGraph with `interrupt_before=["review"]` and a resumable
SQLite checkpointer (`backend/data/checkpoints.sqlite`), so a killed run resumes at the review pause.

---

## 9. Configuration & providers

- Provider and all tunables live in `backend/config.yaml` (no secrets). Default provider is `mock`.
- For a real run: put `ANTHROPIC_FOUNDRY_API_KEY` in `backend/.env`, set `provider: foundry` in
  `config.yaml` (or pass `--provider foundry`). An Azure OpenAI path (`provider: azure`,
  `AZURE_OPENAI_*`) is also wired. Switching providers changes no pipeline code.

---

## 10. Tests

```bash
cd backend && python -m pytest       # 293 backend tests
cd frontend && npm test              # 9 frontend tests
```

---

## Notes

- **Secrets never enter git.** `backend/.env` is git-ignored; only `backend/.env.example` (blank) is committed.
- Generated/local artifacts (`backend/data/`, `backend/handoff/`, `backend/.cache/`, `backend/.venv/`,
  `frontend/node_modules/`, `frontend/dist/`) are git-ignored and recreated on demand.
- Internal Zensar proof-of-concept; the bundled datasets are synthetic, illustrative, not production data.

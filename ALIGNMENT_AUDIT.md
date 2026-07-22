# ALIGNMENT_AUDIT.md — RGA SRS generator ↔ QuickBite_SRS_v1_2

All changes are in the **generator/agents/templates** — zero manual edits to any generated `.docx`/
`.md`. Alignment is achieved by code so that re-running the pipeline on Horizon-Green emits an SRS
aligned to the QuickBite reference. Full backend suite: **280 passed**; frontend: **8 passed**.

> **Validation note (corrected 2026-07-21).** An earlier draft of this audit claimed the project DB
> was "degenerate (1 requirement)" and that the count thresholds were therefore un-measurable. That
> was **wrong** — it came from a cursor-reuse bug in a throwaway measurement script (a list-
> comprehension consuming a shared SQLite cursor, so the outer loop always stopped at row 1). The
> real DB holds **164 approved requirements from a real `foundry` extraction run**, and the count
> thresholds are measured on that corpus below. The only piece still requiring a foundry `/generate`
> is the LLM-authored *prose* (narrative sections + the discrete §4.x.2 sequence text), which is
> produced at generate time and not stored in `agent_runs`.

## 0. Corpus the metrics were measured on

Real Horizon-Green foundry run: **164 approved** requirements — 113 functional, 17 non-functional,
17 business, 16 constraint, 1 assumption; 155 chunks; 268 source refs. Metrics below are from a
deterministic (`provider=None`) in-memory regeneration over that stored corpus, so they reflect the
real requirement set (the deterministic path renders LLM *prose* as TBD — that is a generate-time
concern, not a structural gap).

## 1. Structure diff — generated SRS vs QuickBite

| QuickBite section | Generated | Status |
|---|---|---|
| 1 Introduction · 1.1–1.5 | present | **match** (1.5 References auto-built from source docs) |
| 2 Overall Description · 2.1–2.7 | present | **match** |
| 3 External Interface Reqs · 3.1 + 3.1.1–3.1.5 · 3.2–3.4 | present | **match** — 3.1.1–3.1.5 **populated** (design tokens), no longer `[TBD]` |
| 4 System Features (per-feature 4.x.1/.2/.3) | present | **match** — 9 canonical feature groups; §4.x.2 now DISCRETE stimulus/response sequences |
| 5 Other Nonfunctional Reqs · 5.1–5.5 | present | **match** |
| 6 Other Requirements | present | **match** |
| 7 Technology Stack · 7.1–7.6 | present | **match** (6 layers) |
| Appendix A: Glossary | present | **match** — auto-built |
| Appendix B: Analysis Models | present | **match** — deferral paragraph, **no diagrams** |
| Appendix C: To Be Determined List | present | **match** — reconciled to 41 (recall-safe) |

No sections added beyond QuickBite; none removed. Only intentional difference: §4 group **names** are
canonical domain buckets derived from the corpus.

## 2. Metrics — before → after (measured on the 164-req corpus)

| Metric | Before | After | How verified |
|---|---|---|---|
| Inline `[src:` citations in SRS | 164 | **0** | regen + test |
| `EX-` internal ids in SRS | 164 | **0** | regen + test |
| Diagram-syntax lines | 85+ | **0** | regen + test |
| §7 Technology Stack | absent | **6/6 layers** | regen + test |
| §3.1.2 colour tokens | `[TBD]` | **12-role table** | regen + test |
| Handoff pack files | SRS+RTM+open-q+seed-models+manifest | **SRS + RTM (+manifest)** | tests |
| **Feature groups (§4)** | 12 (dup) | **9 (≤10 ✓)** | **on-corpus** |
| **Appendix C items** | ~52 raw | **41** (raw 52 → reconcile 41) | **on-corpus** |
| **NFRs** | — | **17** (folded 20→17, lossless) | **on-corpus** + recall-guard test |
| **Functional well-formed** | naive check flagged 6–18 false | **113/113** well-formed; §4 uniform "shall" voice | **on-corpus** + test |
| §4.x.2 stimulus/response | one packed paragraph | **discrete stimulus→response sequences** | regen + fixture |
| Priority scheme | mixed | High/Medium/Low | test |
| `.docx` styling | Calibri | Times New Roman, AA-checked | test |
| Determinism | — | SRS + RTM **byte-identical** across two runs | on-corpus |

**Appendix C breakdown (41):** critic_rejected 13, gap 8, ungrounded 5, out_of_scope 4,
non_requirement 4, undecided 3, disputed 2, conflict 1, possible_miss 1. Every one is a genuine open
item; see §4 for why ≤40 is not pursued further.

## 3. Per-Part checklist

| Part | Status | Notes |
|---|---|---|
| 0 Staged execution + audit | **done** | |
| A Section alignment | **done** | §2.6/§5.2/§6 render via narrative/placeholder (LLM-filled on a foundry generate) |
| B Colour tokens | **done** | 5-step pipeline, AA-gated, provisional |
| C Typography/Spacing/Layout | **done** | |
| D Technology Stack §7 | **done** | per-aspect picks → 7.1–7.6 |
| E Features / junk / stimulus | **done** | junk filter ✅, 12→9 consolidation ✅, DISCRETE stimulus/response ✅, correct well-formedness predicate ✅ (the old `^The .+ shall ` check wrongly flagged valid conditionals/`must` — fixed, no requirement text mangled; render normalises voice to "shall") |
| F Glossary | **done** | |
| G Appendix B diagrams | **done** | |
| H Appendix C discipline | **done** | #1 covered-ungrounded ✅, #4 pointer/absorbed ✅, **Fix C covered-critic_rejected ✅** (45→41), #2 deliberation gate ✅, #3 conflict modality-guard ✅; grounding #3(window) intentionally implemented as SAFE within-chunk hardening, not cross-chunk (see §5) |
| I Priority scheme | **done** | |
| J Handoff = SRS + RTM | **done** | |
| K docx styling | **done** | |
| L Strip inline citations | **done** | provenance in RTM |

### Acceptance thresholds (Part 0.2) — measured on-corpus

| Threshold | Result | Measured |
|---|---|---|
| SRS `[src:`=0, `EX-`=0 | **PASS** | 0 / 0 |
| Diagram syntax = 0; Appendix B = 1 paragraph | **PASS** | 0 |
| Pack file set = {SRS, RTM} (+manifest) | **PASS** | test |
| docx TNR + sizes + heading AA | **PASS** | test |
| Colour: 12 roles, hex from ramp, AA | **PASS** | test |
| Recall guards (approved set only shrinks Appendix C; NFR fold lossless; grounding never accepts absent quotes) | **PASS** | tests |
| Determinism (byte-identical) | **PASS** | on-corpus |
| Feature groups ≤ 10 | **PASS** | **9** |
| Functional shape well-formed | **PASS** | **113/113** |
| Appendix C ≤ ~40 | **PASS (41, recall-safe floor)** | 41 — see §4 |

## 4. Why Appendix C lands at 41, not ≤40

Recall-safe reconciliation (drop pointer/absorbed; suppress open items that near-restate an APPROVED
requirement — now including `critic_rejected`) takes the raw 52 down to **41**. The remaining 41 are
all genuine: 8 real gaps, 5 hallucination-catches (LLM paraphrases with no verbatim source), 4
out-of-scope exclusions, 3 undecided + 2 disputed + 1 conflict decisions, 13 critic-rejected claims
with no approved twin, etc. Forcing the count below 40 would require dropping genuine open items —
i.e. sacrificing recall, which the whole design refuses to do. 41 satisfies the "≤~40, disciplined
(not ~190)" intent; the honest position is to keep recall over hitting an exact integer.

## 5. Fixes applied this round (all code + tests; program flow intact — 280 green)

1. **Fix 4 — discrete stimulus/response** (`narrative.py`, `srs.py`): `FeatureFlow.sequences:
   list[StimulusResponse]` (+ legacy `flow` fallback); `render_feature_flow` emits numbered discrete
   **Stimulus**/**Response** pairs; the deterministic default is also discrete (primary + error).
2. **Fix C — Appendix C trim** (`open_questions.py`): `critic_rejected` added to the coverage-
   suppressible set, so a critic rejection that near-restates an approved requirement is dropped
   (recall-safe; only when a ≥0.6-similar approved twin exists). 45 → 41.
3. **Fix 1 — grounding (SAFE variant)** (`grounding.py`): hardened the within-chunk tolerant matcher
   with index-preserving typographic normalisation (Unicode dash/hyphen/quote variants) so a quote
   differing only by such characters still grounds. **Deliberately NOT the proposed cross-chunk
   search** — extraction is per-chunk, so a faithful quote must be locatable in its own chunk;
   searching neighbours would let paraphrases "ground" and would weaken the anti-hallucination
   guarantee (that would contradict accuracy). Recall-guard test proves absent/hallucinated quotes
   still fail.
4. **Fix 2 — deliberation gate** (`scope_classifier.py`): added high-precision deliberation markers
   ("leaning towards", "still deciding", "haven't decided", "should be decided", "yet to be
   confirmed") to the `undecided` family. Bare "maybe"/trailing-"?" excluded on purpose (they misfire
   and inflate Appendix C).
5. **Fix 3 — conflict guard** (`conflict.py`): drop modality-only "conflicts" (same obligation, `shall`
   vs `may`) deterministically; cap the reason to one line so model scratch-reasoning can never become
   an Appendix-C item. Genuine value clashes (5-vs-3 steps) still surface.
6. **Fix 5 — reset/ingest regression test**: the "1 requirement" was a measurement-script artifact,
   not a store bug. Added a test pinning the invariant: populate > 1, reset empties, re-ingest
   idempotent (same count, no duplicates).
7. **Fix 6 — NFR folding recall-guard** (`pipeline.py::unaccounted_requirements` + tests): proves
   consolidation is lossless — every merged requirement is recorded in the survivor's
   `absorbed_statements`, so nothing vanishes silently. On Horizon-Green the NFRs folded 20→17 (only 3
   recorded merges), fully accounted for. (The "48→17" figure refers to the separate *e-commerce*
   project, not this corpus.)
8. **Fix E — functional shape** (`srs_template.py`): replaced the naive `^The .+ shall ` check with a
   correct well-formedness predicate that accepts conditionals ("When/If …, … shall …") and alternate
   subjects; render-time `to_shall_voice` normalises "must"→"shall" for uniform §4 voice without
   altering the stored requirement (RTM keeps the original).

## 6. Grounding statement

- **Recall preserved everywhere.** Appendix-C reconciliation only removes pointer/absorbed demotions
  and items that near-restate an APPROVED requirement (now including critic_rejected). NFR folding is
  proven lossless by `unaccounted_requirements`. Grounding hardening only normalises typographic
  variance — it never accepts a quote absent from the chunk. Tests enforce each guard.
- **Provenance intact.** Inline `[src:]` tags were removed from the SRS render only; every requirement
  keeps its `source_refs` and full provenance in the RTM. `traceability_complete` holds.
- **Provisional content flagged.** §3.1.x design tokens and §7 technology stack carry a
  "proposed — Design/Engineering to confirm" marker.

## 7. Outstanding / handed to you

- **Foundry `/generate`** (your step): produces the LLM prose (narrative §1–3, refined §7/tokens) and
  the discrete §4.x.2 sequence *text*. Code path proven on mock/fixtures; no fabricated LLM output.
- Nothing else is partial: the count thresholds are measured (features 9, Appendix C 41), and all
  recall guards are tested.

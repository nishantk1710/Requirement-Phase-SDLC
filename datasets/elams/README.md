# ELAMS — Evaluation Corpus (flagship)

A verified, synthetic evaluation corpus for the RGA PoC. Domain: **Employee Leave &
Attendance Management System** — universal, low-jargon, and rich enough to exercise
every requirement type. See `../../../evaluation-framework.md` for the framework this
instantiates.

## Provenance
- **Fully synthetic** — no real client data, no PII/IP. Safe to send to an LLM.
- **Structure modelled on real RE material** (PURE-style SRS/notes: sectioned specs,
  conversational transcripts, contradictory emails, terse survey answers).
- **Ground truth by construction** — every `quote` in `gold.json` is embedded
  **verbatim** in its document, so labels are exact, not guessed. Implicit
  requirements cite the supporting span that appears verbatim.

## Files
| File | What |
|---|---|
| `manifest.json` | The 6 documents (doc_id, file, source_type). |
| `docs/` | The source documents across all 6 input modalities. |
| `gold.json` | Canonical requirements: statement, type, difficulty, source span(s), implicit flag, duplicate/conflict links. |
| `catalog.md` | Register of planted hard cases. |
| `splits.json` | Sealed dev / test partition. |
| `verification.md` | Independent blind re-labeling cross-check (TC1.5). |

## Coverage (verified by TC1.2)
- **Modalities (6):** BRD · transcript · email · survey/form · Jira · legacy.
- **Types (5):** functional · non-functional (ISO/IEC 25010) · business · constraint · assumption.
- **Difficulties (6):** explicit · multi-span · implicit · ambiguous · duplicate · conflicting.
- 41 requirements, realistically skewed (most functional/explicit; a handful of each hard case). *(38 original + 3 added at the human review gate.)*

## Reuse recipe (mint a corpus for any project)
1. Pick a domain; write its **canonical requirement set** first (statements + types).
2. For each requirement choose a target document and the **exact sentence** that will carry it (the `quote`).
3. Author the 6 modality documents, **embedding those exact sentences** amid realistic noise/tangents.
4. Plant hard cases (multi-span / implicit / ambiguous / duplicate / conflicting) and record them in `catalog.md`.
5. Fill `gold.json`, `manifest.json`, `splits.json`; run the P1 validators (`tests/test_tc1_dataset.py`) until green.
6. Run the independent blind re-label cross-check; record in `verification.md`.

Same format, same tooling, any domain — that is the reusable part.

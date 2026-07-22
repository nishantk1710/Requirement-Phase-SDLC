"""P6 — human review gate.

The review layer is the accountability boundary of the PoC: a human accepts / edits /
rejects every candidate requirement, every decision is logged (before/after + actor +
timestamp), and NOTHING flows to the generators (P7) until it is `approved`.

  * `gate.py`    — pure, deterministic rules (what is visible downstream; is generation allowed).
  * `service.py` — applies a decision to the store and writes the audit record.
"""

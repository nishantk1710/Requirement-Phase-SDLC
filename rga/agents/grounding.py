"""Deterministic grounding — the real anti-hallucination guard.

Given an LLM-proposed quote, we LOCATE its exact span in the chunk and return the
CHUNK'S OWN bytes for that span plus offsets. A quote counts only if:
  * it can be located in the chunk (exact, or tolerant of case / whitespace / smart quotes), AND
  * the located span is SUBSTANTIVE (>= MIN_QUOTE_CHARS and >= MIN_QUOTE_WORDS).
This makes "no verbatim quote -> no requirement" a code-enforced, testable invariant —
independent of the LLM critic. What we store is the source's bytes, never the model's text.
"""

from __future__ import annotations

import re

# Trivial fragments like "The" must not count as grounding.
MIN_QUOTE_CHARS = 12
MIN_QUOTE_WORDS = 3

# 1:1 character replacements (index-preserving) so located offsets map to the source. Every
# mapping MUST be single-char -> single-char: the located (start, end) are used to slice the
# SOURCE's own bytes, so any length change here would corrupt the stored offsets. This normalises
# only *typographic variants* of the same character — smart quotes, and the several Unicode
# dash/hyphen code points editors emit (non-breaking hyphen, figure dash, minus sign, …) — so a
# quote that differs from the source ONLY by such a variant still grounds. It deliberately does NOT
# touch whitespace (the tolerant matcher below already spans any Unicode whitespace run via `\s+`)
# and does NOT remove zero-width/soft-hyphen characters (removal is not length-preserving).
#
# Grounding stays STRICTLY WITHIN THE ORIGINATING CHUNK by design. Extraction is per-chunk (the
# model only ever saw this chunk), so a faithful quote must be locatable here; searching
# neighbouring chunks would let a quote the model never read "ground" and would weaken the
# anti-hallucination guarantee. This hardening reduces FALSE ungrounded flags (typographic variance)
# without that risk.
_TRANSLATE = str.maketrans({
    "’": "'", "‘": "'", "‚": "'", "‛": "'", "′": "'", "´": "'", "`": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "–": "-", "—": "-", "‐": "-", "‑": "-", "‒": "-", "―": "-", "−": "-",
})


def _pre(s: str) -> str:
    """Index-preserving normalization (typographic quote/dash variants only — no length change)."""
    return s.translate(_TRANSLATE)


def locate_span(quote: str, text: str) -> tuple[int, int] | None:
    """Return (start, end) offsets of `quote` within `text`, or None.

    Tries an exact substring first; then a tolerant match (case-insensitive,
    whitespace-flexible, smart-quote-normalized) that still yields true offsets into
    `text` (because `_pre` is 1:1 and length-preserving)."""
    if not quote:
        return None
    i = text.find(quote)
    if i != -1:
        return (i, i + len(quote))
    q, t = _pre(quote), _pre(text)
    tokens = q.split()
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(tok) for tok in tokens)
    m = re.search(pattern, t, flags=re.IGNORECASE | re.DOTALL)
    return (m.start(), m.end()) if m else None


def is_substantive(span_text: str) -> bool:
    return len(span_text) >= MIN_QUOTE_CHARS and len(span_text.split()) >= MIN_QUOTE_WORDS


def valid_spans(quotes: list[str], text: str) -> list[tuple[str, int, int]]:
    """Locate each quote in `text`; keep only substantive ones. Returns
    (source_slice, start, end) using the SOURCE's own bytes, deduped by (start, end)."""
    out: list[tuple[str, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for q in quotes:
        span = locate_span(q, text)
        if span is None:
            continue
        start, end = span
        slice_ = text[start:end]
        if not is_substantive(slice_):
            continue
        if (start, end) in seen:
            continue
        seen.add((start, end))
        out.append((slice_, start, end))
    return out

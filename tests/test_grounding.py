"""Unit tests for the deterministic grounding guard (rga/agents/grounding.py)."""

from __future__ import annotations

from rga.agents.grounding import is_substantive, locate_span, valid_spans

TEXT = "The system shall allow login. Passwords must be at least 8 characters."


def test_exact_match_offsets():
    assert locate_span("The system shall allow login.", TEXT) == (0, 29)
    assert TEXT[0:29] == "The system shall allow login."


def test_tolerant_case_and_whitespace():
    span = locate_span("the   SYSTEM shall allow LOGIN.", TEXT)
    assert span is not None
    s, e = span
    assert TEXT[s:e] == "The system shall allow login."  # returns SOURCE bytes


def test_not_found_returns_none():
    assert locate_span("a requirement that is not in the text", TEXT) is None


def test_substantive_rejects_trivial():
    assert not is_substantive("The")
    assert not is_substantive("at least")
    assert is_substantive("The system shall allow login.")


def test_valid_spans_keeps_only_substantive_source_bytes():
    spans = valid_spans(["THE SYSTEM SHALL ALLOW LOGIN.", "The", "nope not here"], TEXT)
    assert len(spans) == 1  # trivial "The" and the absent one are dropped
    slice_, s, e = spans[0]
    assert slice_ == TEXT[s:e] == "The system shall allow login."


# --- Fix 1 (SAFE within-chunk hardening): typographic dash/quote variants ground; offsets stay true;
#     genuinely-absent (hallucinated) quotes still fail (anti-hallucination guarantee preserved). ---

# Source uses a non-breaking hyphen (U+2011), a minus sign (U+2212) and a curly apostrophe.
VARIANT_TEXT = "The system shall e‑mail a pre−populated receipt to the customer’s address."


def test_dash_and_quote_variants_ground_within_chunk():
    # The model copied the same sentence with ordinary ASCII hyphen/minus/apostrophe.
    quote = "The system shall e-mail a pre-populated receipt to the customer's address."
    span = locate_span(quote, VARIANT_TEXT)
    assert span is not None
    s, e = span
    # returns the SOURCE's own bytes at true offsets (variant chars preserved), never the model's text
    assert VARIANT_TEXT[s:e] == VARIANT_TEXT
    assert "‑" in VARIANT_TEXT[s:e] and "−" in VARIANT_TEXT[s:e]


def test_offsets_are_index_preserving_for_a_trailing_span():
    quote = "the customer's address."   # ASCII apostrophe; the source has a curly one
    span = locate_span(quote, VARIANT_TEXT)
    assert span is not None
    s, e = span
    assert VARIANT_TEXT[s:e] == "the customer’s address."  # SOURCE bytes (curly apostrophe kept)
    assert e == len(VARIANT_TEXT)  # span reaches the true end — offsets were not shifted by translation


def test_hallucinated_quote_still_fails_after_hardening():
    # A statement the source never contains must NOT ground — hardening only normalises typographic
    # variance, it does not search other text or accept paraphrases.
    assert locate_span("The system shall support two-factor authentication by SMS.", VARIANT_TEXT) is None
    assert valid_spans(["The system shall offer a loyalty points programme."], VARIANT_TEXT) == []

"""TC0.2 — structured() returns validated output, repairs invalid replies, and
NEVER lets unvalidated output escape.

Goal:
  * a valid JSON reply -> a validated Pydantic model;
  * a malformed / wrong-shape reply -> a bounded repair loop recovers;
  * persistently bad output -> raises LLMValidationError (never returns junk).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from rga.llm.base import LLMProvider, _extract_json
from rga.llm.errors import LLMTruncationError, LLMValidationError
from rga.llm.mock import MockProvider


class Demo(BaseModel):
    id: str
    n: int


def test_valid_reply_returns_validated_model():
    p = MockProvider(responses=['{"id": "REQ-1", "n": 3}'])
    out = p.structured("sys", "user", Demo)
    assert isinstance(out, Demo)
    assert out.id == "REQ-1" and out.n == 3


def test_malformed_then_valid_is_repaired():
    # first reply is not JSON at all; second is valid -> repair loop recovers
    p = MockProvider(
        responses=["not json at all", '{"id":"REQ-2","n":7}'],
        max_repair=2,
        base_backoff_s=0.0,
    )
    out = p.structured("sys", "user", Demo)
    assert out.n == 7
    assert p.calls == 2  # took exactly one repair


def test_wrong_shape_then_valid_is_repaired():
    # valid JSON but missing 'n', then corrected
    p = MockProvider(responses=['{"id":"x"}', '{"id":"y","n":1}'], max_repair=2)
    out = p.structured("s", "u", Demo)
    assert out.id == "y" and out.n == 1


def test_json_embedded_in_prose_is_extracted():
    p = MockProvider(responses=['Sure! Here you go:\n{"id":"z","n":9}\nThanks.'])
    assert p.structured("s", "u", Demo).n == 9


def test_markdown_fenced_json_is_extracted():
    p = MockProvider(responses=['```json\n{"id":"m","n":5}\n```'])
    assert p.structured("s", "u", Demo).n == 5


def test_persistently_invalid_raises_and_never_returns_junk():
    p = MockProvider(responses=["bad", "still bad", "nope", "nah"], max_repair=2)
    with pytest.raises(LLMValidationError):
        p.structured("s", "u", Demo)
    # max_repair=2 => 1 initial + 2 repairs = 3 attempts
    assert p.calls == 3


# --- B-M1: truncation handling + top-level-array rejection -------------------
def test_extract_json_rejects_top_level_array():
    """A top-level JSON array must be rejected, not silently reduced to its first element (M2)."""
    with pytest.raises(ValueError):
        _extract_json('[{"id": "a", "n": 1}, {"id": "b", "n": 2}]')
    assert _extract_json('{"id": "a", "n": 1}') == {"id": "a", "n": 1}


def test_structured_grows_budget_on_truncation():
    """A truncated reply (LLMTruncationError) makes structured() RETRY WITH A LARGER budget rather
    than re-prompt into the same truncation (B-M1)."""
    seen: list[int] = []

    class _TruncOnceProvider(LLMProvider):
        def _raw_complete(self, system, user, *, temperature, max_tokens, timeout_s=None):
            seen.append(max_tokens)
            if max_tokens < 4000:
                raise LLMTruncationError(f"truncated at {max_tokens}")
            return '{"id": "REQ-9", "n": 9}'

    p = _TruncOnceProvider(max_tokens=2048, max_repair=3, base_backoff_s=0.0)
    out = p.structured("s", "u", Demo)
    assert out.id == "REQ-9" and out.n == 9
    assert seen[0] == 2048 and seen[-1] >= 4000    # budget grew after the truncation

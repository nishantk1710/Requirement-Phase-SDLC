"""Fills LLM-layer test gaps: JSON extraction robustness, provider transient/fatal
error classification, and the caching provider."""

from __future__ import annotations

import pytest

from rga.llm.base import _extract_json
from rga.llm.cache import CachingProvider
from rga.llm.errors import TransientLLMError
from rga.llm.foundry import FoundryProvider
from rga.llm.mock import MockProvider


# --- _extract_json -----------------------------------------------------------
def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_prose_wrapped():
    assert _extract_json('Sure, here:\n{"a": 1, "b": 2}\nThanks!') == {"a": 1, "b": 2}


def test_extract_json_second_object_ignored():
    # raw_decode stops after the first complete object
    assert _extract_json('{"a": 1}\n{"b": 2}') == {"a": 1}


def test_extract_json_fenced():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_none_raises():
    with pytest.raises(ValueError):
        _extract_json("there is no json object here")


# --- provider error classification (foundry) ---------------------------------
class _FakeMessages:
    def __init__(self, exc):
        self.exc = exc

    def create(self, **_kw):
        raise self.exc


class _FakeClient:
    def __init__(self, exc):
        self.messages = _FakeMessages(exc)


def _foundry_raising(exc):
    p = FoundryProvider(api_key="k", endpoint="https://x", deployment="m")
    p._client = _FakeClient(exc)  # bypass real client
    return p


def test_foundry_transient_by_error_name():
    exc = type("RateLimitError", (Exception,), {})("429")
    p = _foundry_raising(exc)
    with pytest.raises(TransientLLMError):
        p._raw_complete("s", "u", temperature=0.0, max_tokens=10)


def test_foundry_transient_by_5xx_status():
    exc = Exception("server error")
    exc.status_code = 503
    p = _foundry_raising(exc)
    with pytest.raises(TransientLLMError):
        p._raw_complete("s", "u", temperature=0.0, max_tokens=10)


def test_foundry_fatal_is_reraised():
    p = _foundry_raising(ValueError("bad request"))
    with pytest.raises(ValueError):
        p._raw_complete("s", "u", temperature=0.0, max_tokens=10)


# --- caching -----------------------------------------------------------------
def test_cache_serves_repeat_without_second_call():
    inner = MockProvider(responses=['{"x": 1}'])  # only ONE scripted response
    cp = CachingProvider(inner)
    first = cp.complete("sys", "user")
    second = cp.complete("sys", "user")  # identical -> cache hit
    assert first == second == '{"x": 1}'
    assert inner.calls == 1  # inner called only once
    assert cp.hits == 1 and cp.misses == 1


def test_cache_persists_to_disk(tmp_path):
    inner = MockProvider(responses=['{"y": 2}'])
    CachingProvider(inner, cache_dir=str(tmp_path)).complete("sys", "user")
    # a fresh caching provider over a DIFFERENT (empty) inner still hits the disk cache
    inner2 = MockProvider(responses=[])  # would return "{}" if called
    cp2 = CachingProvider(inner2, cache_dir=str(tmp_path))
    assert cp2.complete("sys", "user") == '{"y": 2}'
    assert cp2.hits == 1 and inner2.calls == 0

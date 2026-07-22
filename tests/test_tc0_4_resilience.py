"""TC0.4 — Resilient LLM calls: retry transient errors with backoff, hard-stop
after N attempts, and trip the per-attempt timeout on a hang.

Goal:
  * transient errors are retried (with backoff) then succeed;
  * persistent transient errors hard-stop after `max_attempts` (bounded);
  * a hung call trips the timeout and does not block for the full hang.
Also validates the bounded convergence loop (anti-runaway).
"""

from __future__ import annotations

import time

import pytest

from rga.llm.errors import LLMTimeoutError, TransientLLMError
from rga.llm.mock import MockProvider
from rga.util.loop import run_until_convergence


def test_retries_transient_then_succeeds():
    backoffs: list[float] = []
    p = MockProvider(
        responses=[TransientLLMError("503"), TransientLLMError("503"), "ok"],
        max_attempts=3,
        base_backoff_s=0.01,
        sleep_fn=backoffs.append,  # record instead of sleeping
    )
    assert p.complete("s", "u") == "ok"
    assert p.calls == 3
    assert len(backoffs) == 2  # two backoffs before the successful 3rd attempt


def test_hard_stops_after_max_attempts():
    p = MockProvider(
        responses=[TransientLLMError("x")] * 6,
        max_attempts=3,
        base_backoff_s=0.01,
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(TransientLLMError):
        p.complete("s", "u")
    assert p.calls == 3  # bounded: exactly max_attempts, not 6


def test_timeout_fires_on_hang_without_blocking():
    # each attempt "hangs" 0.4s; timeout is 0.05s -> each attempt trips the timeout;
    # after 3 attempts it hard-stops with LLMTimeoutError, and total time must be far
    # less than 3 * 0.4s (we do not wait for the hangs to finish).
    p = MockProvider(
        responses=[0.4, 0.4, 0.4],
        max_attempts=3,
        timeout_s=0.05,
        base_backoff_s=0.001,
        sleep_fn=lambda _s: None,
    )
    t0 = time.time()
    with pytest.raises(LLMTimeoutError):
        p.complete("s", "u")
    elapsed = time.time() - t0
    assert elapsed < 1.0  # timeouts fired; we did not block for 1.2s of hangs


def test_per_call_timeout_override_both_directions():
    """A per-call `timeout_s` overrides the provider default for THAT call — used by the larger
    tech-stack §7 generation. It can both SHORTEN and (crucially) LENGTHEN the default."""
    # generous default, but a short per-call timeout still trips on a 0.5s call
    p = MockProvider(responses=[0.5], max_attempts=1, timeout_s=30.0, base_backoff_s=0.001,
                     sleep_fn=lambda _s: None)
    with pytest.raises(LLMTimeoutError):
        p.complete("s", "u", timeout_s=0.05)
    # tiny default that WOULD trip a 0.3s call, but the per-call override lengthens past it -> succeeds
    p2 = MockProvider(responses=[0.3], max_attempts=1, timeout_s=0.05, base_backoff_s=0.001,
                      sleep_fn=lambda _s: None)
    assert p2.complete("s", "u", timeout_s=3.0) == "{}"


def test_non_transient_error_is_not_retried():
    p = MockProvider(responses=[ValueError("fatal")], max_attempts=3)
    with pytest.raises(ValueError):
        p.complete("s", "u")
    assert p.calls == 1  # fatal errors are not retried


def test_bounded_loop_stops_on_convergence():
    # produce() returns the same 2 items every pass -> should stop after pass 2
    # (pass 2 adds nothing new), never reaching the cap of 5.
    passes_run = []

    def produce(i):
        passes_run.append(i)
        return [{"id": "A"}, {"id": "B"}]

    out = run_until_convergence(produce, key=lambda x: x["id"], max_passes=5)
    assert {x["id"] for x in out} == {"A", "B"}
    assert len(passes_run) == 2  # pass 1 finds A,B; pass 2 finds nothing new -> stop


def test_bounded_loop_respects_hard_cap():
    # produce() always returns a NEW item -> would run forever; cap must stop it.
    def produce(i):
        return [{"id": f"item-{i}-{k}"} for k in range(3)]

    out = run_until_convergence(produce, key=lambda x: x["id"], max_passes=4)
    assert len(out) == 12  # 3 new per pass * 4 passes, then hard cap stops it

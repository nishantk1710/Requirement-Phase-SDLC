"""LLMProvider — the single interface every agent talks to.

Design:
  * Subclasses implement ONLY `_raw_complete()` (one provider call).
  * The base class owns all the resilience & correctness logic, so every provider
    inherits it identically:
      - `complete()`  : per-attempt timeout + retry with exponential backoff.
      - `structured()`: forces JSON, validates against a Pydantic schema, and
                        re-prompts on failure. It NEVER returns unvalidated output —
                        it returns a validated model or raises `LLMValidationError`.

Timeout note: a per-attempt timeout is enforced with a daemon worker thread
(`join(timeout)`). A hung call therefore trips the timeout without blocking the
process (the daemon thread is abandoned and cannot delay interpreter exit). Real
providers ALSO pass a request timeout to their SDK; this thread guard is the backstop.
"""

from __future__ import annotations

import json
import random
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from ..logging_setup import get_logger
from .errors import LLMTimeoutError, LLMTruncationError, LLMValidationError, TransientLLMError

T = TypeVar("T", bound=BaseModel)
log = get_logger("rga.llm")
_TRUNCATION_MAX_TOKENS = 16384  # upper bound when growing the budget after a truncated reply


class LLMProvider(ABC):
    name: str = "base"

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        base_backoff_s: float = 0.5,
        timeout_s: float = 60.0,
        max_repair: int = 2,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_attempts = max(1, max_attempts)  # never a no-op retry loop
        self.base_backoff_s = base_backoff_s
        self.timeout_s = timeout_s
        self.max_repair = max(0, max_repair)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._sleep = sleep_fn

    # --- provider-specific ---------------------------------------------------
    @abstractmethod
    def _raw_complete(
        self, system: str, user: str, *, temperature: float, max_tokens: int,
        timeout_s: float | None = None,
    ) -> str:
        """One provider call. Raise `TransientLLMError` for retryable failures.
        `timeout_s` (when given) overrides the provider's default request timeout for THIS call —
        used by larger generations (e.g. the tech-stack §7 prose) that legitimately need longer."""

    # --- shared resilience ---------------------------------------------------
    def _call_with_timeout(
        self, system: str, user: str, temperature: float, max_tokens: int,
        timeout_s: float | None = None,
    ) -> str:
        eff = timeout_s or self.timeout_s
        box: dict[str, object] = {}

        def run() -> None:
            try:
                box["value"] = self._raw_complete(
                    system, user, temperature=temperature, max_tokens=max_tokens, timeout_s=eff
                )
            except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
                box["error"] = exc

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(eff)
        if worker.is_alive():
            raise LLMTimeoutError(f"LLM call exceeded {eff}s")
        if "error" in box:
            raise box["error"]  # type: ignore[misc]
        return box["value"]  # type: ignore[return-value]

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> str:
        temperature = self.temperature if temperature is None else temperature
        max_tokens = self.max_tokens if max_tokens is None else max_tokens
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._call_with_timeout(system, user, temperature, max_tokens, timeout_s)
            except (TransientLLMError, LLMTimeoutError) as exc:
                last_exc = exc
                if attempt >= self.max_attempts:
                    break
                backoff = self.base_backoff_s * (2 ** (attempt - 1)) + random.uniform(
                    0, self.base_backoff_s
                )
                log.warning(
                    "LLM attempt %d/%d failed (%s); backing off %.2fs",
                    attempt,
                    self.max_attempts,
                    type(exc).__name__,
                    backoff,
                )
                self._sleep(backoff)
        assert last_exc is not None
        log.error("LLM call failed after %d attempts", self.max_attempts)
        raise last_exc

    def structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> T:
        schema_json = json.dumps(schema.model_json_schema())
        instruction = (
            "Reply with a SINGLE JSON object that validates against this JSON Schema. "
            "Output JSON only — no prose, no markdown fences.\nJSON Schema:\n"
            + schema_json
        )
        prompt = f"{user}\n\n{instruction}"
        eff_max = self.max_tokens if max_tokens is None else max_tokens
        last_err: Exception | None = None
        for repair in range(self.max_repair + 1):
            try:
                raw = self.complete(system, prompt, temperature=temperature, max_tokens=eff_max,
                                    timeout_s=timeout_s)
            except LLMTruncationError as err:
                # truncated JSON is unrepairable by re-prompting — grow the budget and retry (B-M1)
                last_err = err
                grown = min(eff_max * 2, _TRUNCATION_MAX_TOKENS)
                log.warning("structured() truncated at max_tokens=%d; retrying at %d", eff_max, grown)
                if grown == eff_max:
                    break  # already at the cap — give up rather than loop
                eff_max = grown
                continue
            try:
                payload = _extract_json(raw)
                return schema.model_validate(payload)
            except (ValueError, ValidationError) as err:
                last_err = err
                log.warning(
                    "structured() invalid output (repair %d/%d): %s",
                    repair,
                    self.max_repair,
                    err,
                )
                prompt = (
                    f"{user}\n\n{instruction}\n\nYour previous reply was invalid: "
                    f"{err}\nReturn corrected JSON only."
                )
        raise LLMValidationError(
            f"Could not obtain valid {schema.__name__} after "
            f"{self.max_repair + 1} attempt(s): {last_err}"
        )


def _extract_json(text: str) -> dict:
    """Best-effort extraction of a single JSON object from model output.

    Handles clean JSON, markdown-fenced JSON, and JSON embedded in prose.
    """
    t = text.strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        obj = None
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        # a top-level ARRAY is not our object contract — reject it so structured() re-prompts,
        # instead of silently descending into its first element and losing the rest (M2).
        raise ValueError("top-level JSON is an array, expected a single object")
    # Grab the FIRST complete JSON object starting at the first '{'. raw_decode stops at
    # the end of that object, so trailing prose or a second object no longer break parsing.
    start = t.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model output")
    try:
        obj, _ = json.JSONDecoder().raw_decode(t[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"no valid JSON object in model output: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("top-level JSON is not an object")
    return obj

"""CachingProvider — wraps any LLMProvider and caches raw completions by content hash
(in-memory + optional disk). Safe at temperature 0 (deterministic output). Cuts cost and
makes dev re-runs instant. Retries/timeout still apply (this caches at the _raw_complete
level, beneath the base class's resilience loop)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .base import LLMProvider


class CachingProvider(LLMProvider):
    def __init__(self, inner: LLMProvider, cache_dir: str | None = None) -> None:
        super().__init__(
            max_attempts=inner.max_attempts,
            base_backoff_s=inner.base_backoff_s,
            timeout_s=inner.timeout_s,
            max_repair=inner.max_repair,
            temperature=inner.temperature,
            max_tokens=inner.max_tokens,
        )
        self.inner = inner
        self.name = f"cached:{inner.name}"
        self.deployment = getattr(inner, "deployment", None)
        self._mem: dict[str, str] = {}
        self._dir = Path(cache_dir) if cache_dir else None
        if self._dir:
            self._dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def _key(self, system: str, user: str, temperature: float, max_tokens: int) -> str:
        blob = f"{self.deployment}\x00{temperature}\x00{max_tokens}\x00{system}\x00{user}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _raw_complete(self, system: str, user: str, *, temperature: float, max_tokens: int,
                      timeout_s: float | None = None) -> str:
        key = self._key(system, user, temperature, max_tokens)
        if key in self._mem:
            self.hits += 1
            return self._mem[key]
        if self._dir is not None:
            f = self._dir / f"{key}.txt"
            if f.exists():
                val = f.read_text(encoding="utf-8")
                self._mem[key] = val
                self.hits += 1
                return val
        val = self.inner._raw_complete(system, user, temperature=temperature, max_tokens=max_tokens,
                                       timeout_s=timeout_s)
        self._mem[key] = val
        self.misses += 1
        if self._dir is not None:
            (self._dir / f"{key}.txt").write_text(val, encoding="utf-8")
        return val

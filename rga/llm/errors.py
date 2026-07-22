"""LLM error taxonomy. Only `TransientLLMError` and `LLMTimeoutError` are retried."""

from __future__ import annotations


class LLMError(Exception):
    """Base for all LLM-layer errors."""


class TransientLLMError(LLMError):
    """Retryable failure (rate limit, 5xx, connection reset, timeout upstream)."""


class LLMTimeoutError(LLMError):
    """A single attempt exceeded the configured per-attempt timeout."""


class LLMValidationError(LLMError):
    """`structured()` could not obtain schema-valid output within the repair budget."""


class LLMTruncationError(LLMError):
    """A response was cut off at `max_tokens` (stop_reason == "max_tokens"). Not transient — the
    fix is a LARGER budget, so `structured()` retries with a raised max_tokens rather than
    re-prompting identically (which would just truncate again)."""

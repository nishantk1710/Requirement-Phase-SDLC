"""FoundryProvider — Claude (e.g. claude-sonnet-4-6) served via Azure AI Foundry,
using anthropic.AnthropicFoundry. This is the provider the PoC actually uses.

Notes on the Anthropic Messages API (vs OpenAI chat):
  * the system prompt is a TOP-LEVEL `system=` parameter, not a message role;
  * `max_tokens` is required;
  * the response `content` is a list of blocks; we join their `.text`.
The `anthropic` SDK is imported lazily so the mock-based test suite needs no key/SDK.
"""

from __future__ import annotations

import threading

from .base import LLMProvider
from .errors import LLMTruncationError, TransientLLMError

_TRANSIENT_NAMES = {
    "RateLimitError",
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
    "OverloadedError",
}


class FoundryProvider(LLMProvider):
    name = "foundry"

    def __init__(
        self, *, api_key: str | None, endpoint: str | None, deployment: str, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.deployment = deployment
        self._api_key = api_key
        self._endpoint = endpoint
        self._client = None
        self._client_lock = threading.Lock()  # safe lazy init under concurrency
        self.tokens_in = 0
        self.tokens_out = 0

    def _get_client(self):
        if self._client is None:
            with self._client_lock:
                if self._client is None:  # double-checked locking
                    try:
                        from anthropic import AnthropicFoundry
                    except ImportError as exc:  # pragma: no cover - env-dependent
                        raise ImportError(
                            "The 'anthropic' package (with AnthropicFoundry) is required "
                            "for provider=foundry. Run: pip install -U anthropic"
                        ) from exc
                    if not self._api_key or not self._endpoint:
                        raise ValueError(
                            "ANTHROPIC_FOUNDRY_API_KEY (.env) and a Foundry endpoint "
                            "(config.yaml foundry.endpoint) are required for provider=foundry."
                        )
                    # request timeout is the PRIMARY guard; the base-class thread
                    # timeout is only a backstop.
                    self._client = AnthropicFoundry(
                        api_key=self._api_key,
                        base_url=self._endpoint,
                        timeout=self.timeout_s,
                    )
        return self._client

    def _raw_complete(
        self, system: str, user: str, *, temperature: float, max_tokens: int,
        timeout_s: float | None = None,
    ) -> str:
        client = self._get_client()
        eff_timeout = timeout_s or self.timeout_s
        if timeout_s:  # per-call override for larger generations (e.g. §7 tech-stack prose)
            client = client.with_options(timeout=eff_timeout)
        try:
            message = client.messages.create(
                model=self.deployment,
                system=system or None,
                messages=[{"role": "user", "content": user}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            usage = getattr(message, "usage", None)
            if usage is not None:  # accumulate token usage for cost reporting
                self.tokens_in += int(getattr(usage, "input_tokens", 0) or 0)
                self.tokens_out += int(getattr(usage, "output_tokens", 0) or 0)
            # a reply cut off at the token limit is unusable JSON — signal it so structured() can
            # retry with a LARGER budget instead of re-prompting into the same truncation (B-M1).
            if getattr(message, "stop_reason", None) == "max_tokens":
                raise LLMTruncationError(f"response truncated at max_tokens={max_tokens}")
            parts = [
                getattr(block, "text", "") for block in (message.content or [])
            ]
            return "".join(p for p in parts if p)
        except Exception as exc:  # classify transient vs fatal without hard SDK imports
            status = getattr(exc, "status_code", None)
            if type(exc).__name__ in _TRANSIENT_NAMES or (
                isinstance(status, int) and 500 <= status < 600
            ):
                raise TransientLLMError(str(exc)) from exc
            raise

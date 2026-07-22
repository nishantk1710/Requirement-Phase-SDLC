"""ClaudeProvider — Wave-2 stub. The interface is in place so switching to Claude
is a config change; the implementation lands when we adopt it in Wave 2."""

from __future__ import annotations

from .base import LLMProvider


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self, *, api_key: str | None, model: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.api_key = api_key
        self.model = model

    def _raw_complete(
        self, system: str, user: str, *, temperature: float, max_tokens: int,
        timeout_s: float | None = None,
    ) -> str:
        raise NotImplementedError(
            "ClaudeProvider is a Wave-2 stub. Implement with the Anthropic SDK when "
            "the provider swap is scheduled (see FINAL-Plan §M7)."
        )

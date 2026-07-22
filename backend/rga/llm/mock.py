"""MockProvider — scriptable, credential-free provider for tests and local dev.

`responses` is consumed one item per underlying call:
  * str           -> returned as the raw completion
  * Exception     -> raised (e.g. TransientLLMError to exercise retries)
  * int / float   -> sleeps that many seconds then returns "{}" (to exercise timeouts)
When the script is exhausted it returns "{}".
"""

from __future__ import annotations

import time

from .base import LLMProvider


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, responses: list | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.responses = list(responses or [])
        self.calls = 0

    def _raw_complete(
        self, system: str, user: str, *, temperature: float, max_tokens: int,
        timeout_s: float | None = None,
    ) -> str:
        self.calls += 1
        if not self.responses:
            return "{}"
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            time.sleep(float(item))
            return "{}"
        return str(item)

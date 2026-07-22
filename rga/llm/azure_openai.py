"""AzureOpenAIProvider — real backend. `openai` is imported lazily so the rest of
the system (and the test suite, which uses the mock) has no hard dependency on it."""

from __future__ import annotations

from .base import LLMProvider
from .errors import TransientLLMError


class AzureOpenAIProvider(LLMProvider):
    name = "azure"

    def __init__(
        self,
        *,
        api_key: str | None,
        endpoint: str | None,
        deployment: str,
        api_version: str,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.deployment = deployment
        self._api_key = api_key
        self._endpoint = endpoint
        self._api_version = api_version
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AzureOpenAI
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise ImportError(
                    "The 'openai' package is required for AzureOpenAIProvider "
                    "(pip install openai)."
                ) from exc
            if not self._api_key or not self._endpoint:
                raise ValueError(
                    "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set "
                    "(see .env) to use provider=azure."
                )
            self._client = AzureOpenAI(
                api_key=self._api_key,
                azure_endpoint=self._endpoint,
                api_version=self._api_version,
                timeout=self.timeout_s,
            )
        return self._client

    def _raw_complete(
        self, system: str, user: str, *, temperature: float, max_tokens: int,
        timeout_s: float | None = None,
    ) -> str:
        client = self._get_client()
        if timeout_s:  # per-call timeout override (larger generations, e.g. §7 tech-stack)
            client = client.with_options(timeout=timeout_s)
        # Import concrete error types lazily (only when openai is present).
        from openai import (
            APIConnectionError,
            APIError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )

        try:
            resp = client.chat.completions.create(
                model=self.deployment,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        except (
            RateLimitError,
            APITimeoutError,
            APIConnectionError,
            InternalServerError,
        ) as exc:
            raise TransientLLMError(str(exc)) from exc
        except APIError as exc:
            status = getattr(exc, "status_code", None)
            if status is not None and 500 <= int(status) < 600:
                raise TransientLLMError(str(exc)) from exc
            raise

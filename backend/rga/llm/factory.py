"""Provider factory. Selects the backend from `config.provider` ONLY — the rest of
the pipeline never imports a concrete provider, so swapping is a one-line config change."""

from __future__ import annotations

from ..config import AppConfig, Secrets
from .azure_openai import AzureOpenAIProvider
from .base import LLMProvider
from .claude import ClaudeProvider
from .foundry import FoundryProvider
from .mock import MockProvider


def get_provider(
    config: AppConfig, secrets: Secrets | None = None, **overrides
) -> LLMProvider:
    secrets = secrets or Secrets()
    llm = config.llm
    common = dict(
        max_attempts=llm.max_attempts,
        base_backoff_s=llm.base_backoff_s,
        timeout_s=llm.timeout_s,
        max_repair=llm.max_repair,
        temperature=llm.temperature,
        max_tokens=llm.max_tokens,
    )
    common.update(overrides)

    provider = config.provider.lower()
    if provider == "azure":
        return AzureOpenAIProvider(
            api_key=secrets.azure_openai_api_key,
            endpoint=secrets.azure_openai_endpoint,
            deployment=config.azure.deployment,
            api_version=config.azure.api_version,
            **common,
        )
    if provider == "claude":
        return ClaudeProvider(
            api_key=secrets.anthropic_api_key, model=config.claude.model, **common
        )
    if provider == "foundry":
        return FoundryProvider(
            api_key=secrets.anthropic_foundry_api_key,
            endpoint=(secrets.anthropic_foundry_endpoint or config.foundry.endpoint),
            deployment=config.foundry.deployment,
            **common,
        )
    if provider == "mock":
        return MockProvider(**common)
    raise ValueError(
        f"unknown provider: {config.provider!r} (expected foundry|azure|claude|mock)"
    )

"""TC0.1 — Provider is selected by CONFIG ONLY (zero pipeline-code change).

Goal: flipping `provider` in config returns the right provider class, with the
resilience settings propagated, and unknown providers fail loudly.
"""

from __future__ import annotations

import pytest

from rga.config import AppConfig, Secrets
from rga.llm.azure_openai import AzureOpenAIProvider
from rga.llm.claude import ClaudeProvider
from rga.llm.factory import get_provider
from rga.llm.foundry import FoundryProvider
from rga.llm.mock import MockProvider


def test_factory_selects_by_config_only():
    secrets = Secrets(
        azure_openai_api_key="k",
        azure_openai_endpoint="https://example.openai.azure.com/",
        anthropic_api_key="a",
        anthropic_foundry_api_key="f",
    )
    cases = [
        ("foundry", FoundryProvider),
        ("azure", AzureOpenAIProvider),
        ("claude", ClaudeProvider),
        ("mock", MockProvider),
    ]
    for name, cls in cases:
        cfg = AppConfig(provider=name)
        provider = get_provider(cfg, secrets)
        assert isinstance(provider, cls), f"{name} -> {type(provider).__name__}"
        # resilience config threaded through from AppConfig.llm
        assert provider.max_attempts == cfg.llm.max_attempts
        assert provider.timeout_s == cfg.llm.timeout_s


def test_provider_is_case_insensitive():
    assert isinstance(get_provider(AppConfig(provider="MOCK"), Secrets()), MockProvider)


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_provider(AppConfig(provider="bogus"), Secrets())

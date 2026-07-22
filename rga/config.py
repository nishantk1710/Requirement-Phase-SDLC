"""Configuration loading.

Two sources, kept strictly separate:
  * `config.yaml`  -> non-secret application config (provider choice, model, tuning) -> `AppConfig`
  * `.env`         -> secrets (API keys, endpoints)                                  -> `Secrets`

Nothing secret ever lives in `config.yaml`.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .logging_setup import get_logger

log = get_logger("rga.config")


class _Strict(BaseModel):
    """Reject UNKNOWN keys — a typo in config.yaml (e.g. `provder: foundry`) then fails loud instead
    of being silently dropped, which would quietly run the pipeline on the default `mock` provider (A-F5)."""
    model_config = ConfigDict(extra="forbid")


class AzureCfg(_Strict):
    deployment: str = "gpt-4o"
    api_version: str = "2024-08-01-preview"


class ClaudeCfg(_Strict):
    model: str = "claude-opus-4-8"


class FoundryCfg(_Strict):
    # Claude on Azure AI Foundry (anthropic.AnthropicFoundry). Non-secret; secret key
    # lives in .env as ANTHROPIC_FOUNDRY_API_KEY.
    endpoint: str = "https://gaura-mgt924zq-eastus2.services.ai.azure.com/anthropic/"
    deployment: str = "claude-sonnet-4-6"


class LLMCfg(_Strict):
    temperature: float = 0.0
    max_tokens: int = Field(2048, ge=1)
    max_attempts: int = Field(3, ge=1)
    base_backoff_s: float = Field(0.5, ge=0)
    timeout_s: float = Field(60.0, gt=0)
    max_repair: int = Field(2, ge=0)


class StoreCfg(_Strict):
    path: str = "./data/rga.db"


class LoopCfg(_Strict):
    max_passes: int = Field(3, ge=1)


class AppConfig(_Strict):
    provider: str = "mock"
    azure: AzureCfg = AzureCfg()
    claude: ClaudeCfg = ClaudeCfg()
    foundry: FoundryCfg = FoundryCfg()
    llm: LLMCfg = LLMCfg()
    store: StoreCfg = StoreCfg()
    loop: LoopCfg = LoopCfg()
    # Optional overrides for the SRS/RTM Word (.docx) styling (Part K). Defaults live in
    # generate/docx_style.py::DOCX_STYLE; only the keys set here override them (deep-merged).
    docx: dict = Field(default_factory=dict)


class Secrets(BaseSettings):
    """Secrets from environment / .env. Missing values are allowed (None) so the
    app still boots with provider=mock."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    azure_openai_api_key: str | None = None
    azure_openai_endpoint: str | None = None
    anthropic_api_key: str | None = None
    # Claude on Azure AI Foundry — the provider we actually use.
    anthropic_foundry_api_key: str | None = None
    anthropic_foundry_endpoint: str | None = None  # optional override of config.yaml


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load application config from YAML; fall back to defaults if the file is absent."""
    p = Path(path)
    if not p.exists():
        log.warning("config file %s not found — using defaults (provider=mock)", p)
        return AppConfig()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cfg = AppConfig.model_validate(data)  # raises on an unknown key (see _Strict), never silent
    log.info("config loaded from %s (provider=%s, foundry.deployment=%s)",
             p, cfg.provider, cfg.foundry.deployment)
    return cfg


def load_secrets() -> Secrets:
    return Secrets()

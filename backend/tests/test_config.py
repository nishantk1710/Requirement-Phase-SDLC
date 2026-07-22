"""Config strictness (a typo fails loud instead of silently running mock) + BOM/encoding-tolerant
file reading (messy Windows input never aborts ingest)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rga.config import AppConfig, load_config
from rga.ingest.loaders import read_text_tolerant


def test_config_rejects_unknown_key(tmp_path):
    """A-F5: an unknown/mistyped key raises rather than being silently ignored (which would run the
    default mock provider while appearing configured)."""
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"provder": "foundry"})          # top-level typo
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"foundry": {"endpint": "x"}})   # nested typo


def test_load_config_reads_yaml_and_falls_back_to_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("provider: foundry\nfoundry:\n  deployment: claude-sonnet-4-6\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.provider == "foundry" and cfg.foundry.deployment == "claude-sonnet-4-6"
    assert load_config(tmp_path / "absent.yaml").provider == "mock"   # missing file -> defaults


def test_read_text_tolerant_handles_bom_and_cp1252(tmp_path):
    """A-F4: a UTF-8 BOM is stripped (not left in the first cell), and cp1252 (Windows) input reads
    without raising."""
    bom = tmp_path / "bom.txt"
    bom.write_bytes(b"\xef\xbb\xbfThe system shall allow login.")
    assert read_text_tolerant(bom) == "The system shall allow login."

    win = tmp_path / "win.txt"
    win.write_bytes("Prices in € with “smart” quotes.".encode("cp1252"))
    out = read_text_tolerant(win)
    assert "€" in out and "smart" in out

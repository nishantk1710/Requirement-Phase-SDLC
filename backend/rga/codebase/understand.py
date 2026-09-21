"""The "understanding" half of brownfield support.

Turns a `CodeIndex` (from scan.py) into:
  * a **capability map** — the existing system's modules, one per top-level directory, with their
    size and representative symbols;
  * a **summary** — a short prose description of what the existing system is;
  * **impact** — for a given change requirement, the existing files it most likely touches.

All three are DETERMINISTIC by default (no provider needed), so the feature works and tests offline.
When an LLM provider is supplied, a single best-effort pass refines the summary prose; a failure or
timeout silently keeps the deterministic summary (the code map and impact are never LLM-dependent).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from .scan import CodeIndex

# words too common to signal impact — dropped from a requirement before matching it to code.
_STOP = frozenset("""
a an the this that these those and or but if then else when while for to of in on at by with from as
is are be been being was were will shall should would could can may might must not no nor system user
users allow allows enable enables provide provides support supports able ability it its their them they
each any all every via using use used into over under out up down new add adds added change changes
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def _keywords(text: str) -> list[str]:
    """Significant lowercased tokens from a requirement statement (stopwords/short words removed)."""
    seen: set[str] = set()
    out: list[str] = []
    for w in _WORD.findall(text.lower()):
        if len(w) >= 3 and w not in _STOP and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def _split_identifier(name: str) -> list[str]:
    """Break `resolveSkuVariant` / `resolve_sku_variant` into ['resolve','sku','variant']."""
    return [p.lower() for p in _CAMEL.findall(name) if p]


def _top_module(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "(root)"


def system_capabilities(index: CodeIndex) -> list[dict]:
    """The existing system's modules — one per top-level directory — largest first. Deterministic."""
    groups: dict[str, dict] = {}
    for f in index["files"]:
        mod = _top_module(f["path"])
        g = groups.setdefault(mod, {"files": 0, "loc": 0, "langs": {}, "symbols": []})
        g["files"] += 1
        g["loc"] += f["loc"]
        g["langs"][f["lang"]] = g["langs"].get(f["lang"], 0) + 1
        g["symbols"].extend(f["symbols"][:6])
    caps: list[dict] = []
    for mod, g in groups.items():
        primary = max(g["langs"].items(), key=lambda kv: kv[1])[0] if g["langs"] else "—"
        # de-duplicate representative symbols, order-preserving
        seen: set[str] = set()
        symbols = [s for s in g["symbols"] if not (s in seen or seen.add(s))][:14]
        caps.append({"module": mod, "files": g["files"], "loc": g["loc"],
                     "language": primary, "key_symbols": symbols})
    caps.sort(key=lambda c: (-c["files"], c["module"]))
    return caps


def impact_for(statement: str, index: CodeIndex, *, top_n: int = 4) -> list[dict]:
    """The existing files a change requirement most likely touches. A **symbol-name** match counts
    double (a file that DEFINES `cancelOrder` is a stronger signal than one whose path merely
    contains "order"), and a file is kept only when its score is ≥ 2 — i.e. a symbol hit, or at
    least two distinct keyword hits. This drops lone generic-word matches ("load", "site") that
    otherwise flood the impact list with irrelevant files. Deterministic; empty when nothing is a
    plausible match (an honest 'net-new' signal)."""
    words = set(_keywords(statement))
    if not words:
        return []
    scored: list[tuple[int, int, int, str, str]] = []
    for f in index["files"]:
        path_tokens = {w.lower() for w in re.split(r"[/._-]", f["path"]) if w}
        sym_tokens: set[str] = set()
        for s in f["symbols"]:
            sym_tokens.update(_split_identifier(s))
        sym_hits = words & sym_tokens
        path_hits = words & path_tokens
        score = len(sym_hits) * 2 + len(path_hits)
        if score >= 2:  # a defined-symbol match, or ≥2 keyword hits — never a lone generic word
            matched = len(sym_hits | path_hits)
            scored.append((score, matched, -len(f["path"]), f["path"], f["lang"]))
    scored.sort(reverse=True)
    return [{"path": p, "lang": lang, "matched": m} for _sc, m, _neg, p, lang in scored[:top_n]]


def _deterministic_summary(index: CodeIndex, caps: list[dict]) -> str:
    langs = ", ".join(f"{lang} ({n})" for lang, n in index["languages"].items()) or "—"
    top = ", ".join(c["module"] for c in caps[:6]) or "—"
    return (f"An existing system of {index['n_files']} source file(s) across {len(caps)} top-level "
            f"module(s). Languages: {langs}. Principal modules: {top}.")


# --- optional LLM refinement (best-effort) -----------------------------------
class _SystemSummary(BaseModel):
    summary: str = Field(description="2-4 sentence description of what this existing system does, "
                                     "grounded ONLY in the modules and symbols provided.")


_SUMMARY_SYSTEM = (
    "You are a senior engineer summarising an EXISTING codebase for a change-request specification. "
    "Describe what the system does at a high level, grounded strictly in the module names, file paths "
    "and symbol names given. Do not invent features. Be concise and factual."
)


def _llm_summary(index: CodeIndex, caps: list[dict], provider) -> str | None:
    lines = [f"- {c['module']}/  ({c['files']} files, {c['language']}): "
             f"{', '.join(c['key_symbols'][:10]) or '—'}" for c in caps[:20]]
    readme = (index.get("readme") or "").strip()
    user = "MODULES:\n" + "\n".join(lines) + (f"\n\nREADME (excerpt):\n{readme[:1500]}" if readme else "")
    try:
        # best-effort + bounded: a stalling provider falls back to the deterministic summary fast
        res = provider.structured(_SUMMARY_SYSTEM, user, _SystemSummary,
                                  max_tokens=400, timeout_s=45.0, max_attempts=2)
        return (res.summary or "").strip() or None
    except Exception:
        return None


def understand_codebase(index: CodeIndex, *, provider=None) -> dict:
    """Build a JSON-serialisable understanding of the existing system: capability map + summary +
    language/size stats. Deterministic unless a `provider` refines the summary prose."""
    caps = system_capabilities(index)
    summary = _deterministic_summary(index, caps)
    if provider is not None:
        refined = _llm_summary(index, caps, provider)
        if refined:
            summary = refined
    return {
        "summary": summary,
        "capabilities": caps,
        "languages": index["languages"],
        "n_files": index["n_files"],
        "truncated": index.get("truncated", False),
        "root": index.get("root", ""),
    }

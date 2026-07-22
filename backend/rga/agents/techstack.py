"""Technology-stack agent (SRS §7) — PER-ASPECT candidates for human review.

The stack is broken into ASPECTS (frontend, backend, database, auth/security, hosting, plus
product-specific ones the requirements demand — payments, search, notifications, …). For EACH aspect
the agent populates 2-3 WIDELY-USED, POPULAR candidate technologies, each with a one-line reason, and
marks the ONE that best aligns with this product/SRS as `recommended`. In the review screen the human
picks one candidate per aspect; those picks compose SRS §7 (recommended = the default, so §7 is always
complete). If the raw inputs already NAME a stack, it is adopted instead (one locked candidate/aspect).

The LLM writes the reasoning; a deterministic fallback keeps the pipeline working with the mock
provider / offline. Nothing here is hand-written into a handoff — the generator consumes this.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..llm.base import LLMProvider
from ..models import Chunk, Requirement

# Right-sized so the concise per-aspect output fits without truncating; this one call gets a longer
# per-call timeout than the small extraction calls (larger prose generation on a slow endpoint).
MAX_TOKENS = 3200
TIMEOUT_S = 180.0

# CURATED, low-ambiguity technology tokens — used ONLY to detect a stack the inputs already NAME.
# Excludes words that collide with ordinary prose (bare "go"/"mean"/"spring"/"express"/"rails").
_TECH_TOKENS: dict[str, tuple[str, ...]] = {
    "frontend": ("react", "angular", "vue", "svelte", "next.js", "nextjs", "flutter",
                 "react native", "tailwind"),
    "backend": ("node.js", "nodejs", "express.js", "django", "flask", "fastapi", "spring boot",
                "springboot", "asp.net", "laravel", "golang", "ruby on rails", "nest.js", "nestjs",
                "java", "python"),
    "data": ("postgresql", "postgres", "mysql", "mariadb", "mongodb", "dynamodb", "cassandra",
             "redis", "sqlite", "sql server", "elasticsearch"),
    "infra": ("kubernetes", "docker", "terraform", "kafka", "rabbitmq", "graphql", "grpc",
              "socket.io", "aws", "azure", "gcp"),
}
_STACK_NAMES = ("mern", "mean", "lamp", "jamstack", "lemp")
_STACK_CUES = ("tech stack", "technology stack", "built on", "built with", "built using",
               "implemented using", "implemented on", "developed using", "developed on",
               "will be built", "shall be built on", "runs on node", "on the mern", "on the mean")

# Requirement signals that (a) tilt the backend recommendation and (b) decide which product-specific
# aspects to include. Deterministic-fallback heuristics; the LLM reasons for itself.
_PY_SIGNALS = ("agent", "agentic", "llm", "machine learning", "ml model", "data pipeline", "nlp",
               "recommendation engine", "forecast", "analytics")
_JS_SIGNALS = ("real-time", "realtime", "websocket", "chat", "collaborative", "single-page",
               "spa", "live update", "dashboard")
_PAY_SIGNALS = ("payment", "checkout", "card", "gst", "invoice", "cod", "cash on delivery", "wallet", "refund")
_SEARCH_SIGNALS = ("search", "faceted", "catalogue", "catalog", "filter", "browse")
_NOTIFY_SIGNALS = ("notification", "notify", "email", "sms", "otp", "alert")


class TechCandidate(BaseModel):
    name: str                            # e.g. "React", "PostgreSQL"
    recommended: bool = False
    reason: str = ""                     # what it is + why it fits THESE requirements + its popularity


class TechAspect(BaseModel):
    key: str                             # stable id, e.g. "frontend", "backend", "database", "payments"
    title: str                           # display, e.g. "Frontend / Client"
    rationale: str = ""                  # why this aspect matters for THIS product
    candidates: list[TechCandidate] = Field(default_factory=list)   # 1 (adopted) or 2-3 (proposed)


class TechStackResult(BaseModel):
    stated_in_inputs: bool = False
    basis: str = ""
    aspects: list[TechAspect] = Field(default_factory=list)


# --- detection (is a stack already NAMED in the inputs?) ---------------------
def _corpus_text(chunks: list[Chunk]) -> str:
    return "\n".join(c.text for c in chunks).lower()


def detect_stated_tokens(text: str) -> list[str]:
    """Curated technology tokens present in the source text (whole-word), plus '<name> stack' names."""
    found: list[str] = []
    for group in _TECH_TOKENS.values():
        for tok in group:
            pat = re.escape(tok.strip())
            if re.search(rf"(?<![a-z0-9]){pat}(?![a-z0-9])", text) and tok.strip() not in found:
                found.append(tok.strip())
    for name in _STACK_NAMES:
        if f"{name} stack" in text and name not in found:
            found.append(name)
    return found


def _is_stated(text: str, tokens: list[str]) -> bool:
    """A stack is 'stated' ONLY with an explicit declaration: a '<name> stack' phrase, or a
    declaration cue ('tech stack', 'built with/on/using', …) plus a concrete technology token."""
    if any(f"{n} stack" in text for n in _STACK_NAMES):
        return True
    if not tokens:
        return False
    return any(cue in text for cue in _STACK_CUES)


# --- LLM prompts -------------------------------------------------------------
_BREVITY = (
    " Be CONCISE: each candidate `reason` and each aspect `rationale` is ONE sentence. Concrete and "
    "specific, no marketing language."
)
_PROPOSE_SYSTEM = (
    "You are a solutions architect proposing a technology stack for HUMAN REVIEW, for a team that will "
    "BUILD the system from scratch in an implementation phase. Break the stack into the ASPECTS this "
    "product needs. ALWAYS cover: frontend/client, backend/API, database/persistence, authentication & "
    "security, hosting/infrastructure. ALSO add the product-specific aspects the requirements demand "
    "(e.g. payments, search, notifications/messaging, file/media storage, real-time, analytics) — and "
    "ONLY those the requirements justify.\n"
    "For EACH aspect propose 2-3 candidates that are SIMPLE, GENERAL-PURPOSE, and EASY TO BUILD WITH: "
    "mainstream languages, frameworks, libraries, databases and open standards that a small team can "
    "implement directly and hire for easily. Think building blocks like React, Node.js + Express, "
    "Python + FastAPI, PostgreSQL, MySQL, MongoDB, Redis, JWT, OAuth 2.0, Docker.\n"
    "Do NOT propose heavyweight all-in-one PLATFORMS or managed SaaS products that REPLACE building the "
    "system (e.g. avoid Shopify/Medusa/Vendure/commerce platforms, low-code suites), and do NOT propose "
    "niche or trendy frameworks when a plain, well-known library or framework does the job. Prefer the "
    "simplest option that satisfies the requirements over the most feature-rich one.\n"
    "Each candidate `name` is a SHORT, recognizable technology name (1-4 words) — e.g. 'React', "
    "'Node.js + Express', 'Python + FastAPI', 'PostgreSQL', 'JWT + bcrypt' — never a long descriptive "
    "phrase. Give each candidate a one-sentence `reason` (what it is, why it fits THESE requirements, "
    "and that it is a mainstream, easily-buildable choice). Mark EXACTLY ONE candidate per aspect as "
    "recommended=true: the SIMPLEST widely-used option that meets this product's requirements. Give "
    "each aspect a one-sentence `rationale`. Use short, stable, lowercase `key`s (frontend, backend, "
    "database, auth, hosting, payments, search, notifications, …). Set stated_in_inputs=false and "
    "summarise the deciding factors in `basis`." + _BREVITY
)
_STATED_SYSTEM = (
    "You are a solutions architect writing the Technology Stack section of an SRS. The source "
    "documents ALREADY name a technology stack. For each aspect the sources specify (frontend, "
    "backend, database, auth, hosting, and any product-specific ones), create an aspect with a SINGLE "
    "candidate — the named technology — recommended=true, its `reason` citing the source. Do NOT add "
    "alternatives or technologies the sources do not mention. Use short lowercase `key`s. Set "
    "stated_in_inputs=true; `basis` names the source-stated stack." + _BREVITY
)


def _digest(project_name: str, requirements: list[Requirement], hints: list[str]) -> str:
    feats: list[str] = []
    for r in requirements:
        f = (r.feature or "").strip()
        if f and f not in feats:
            feats.append(f)
    nfr = sorted({(r.nfr_category or "").strip() for r in requirements
                  if r.rtype.value == "non_functional" and r.nfr_category})
    sample = [r.statement for r in requirements[:45]]
    parts = [f"PROJECT: {project_name}",
             f"FEATURES ({len(feats)}): " + ", ".join(feats[:40]),
             "NFR CATEGORIES: " + (", ".join(nfr) or "none tagged")]
    if hints:
        parts.append("TECHNOLOGY TERMS FOUND IN SOURCES: " + ", ".join(hints))
    parts.append("REQUIREMENT SAMPLE:\n- " + "\n- ".join(s[:160] for s in sample))
    return "\n".join(parts)


# --- deterministic fallback (mock / offline) ---------------------------------
def _c(name: str, reason: str, rec: bool = False) -> TechCandidate:
    return TechCandidate(name=name, reason=reason, recommended=rec)


def _fallback(project_name: str, requirements: list[Requirement], tokens: list[str], stated: bool) -> TechStackResult:
    if stated:
        # group the named tokens into aspects; each becomes a single locked candidate
        buckets = {"frontend": "Frontend / Client", "backend": "Backend / API",
                   "data": "Database / Persistence", "infra": "Hosting / Infrastructure"}
        aspects: list[TechAspect] = []
        for grp, title in buckets.items():
            named = [t for t in tokens if t in _TECH_TOKENS[grp]]
            if named:
                aspects.append(TechAspect(
                    key=grp if grp != "data" else "database", title=title,
                    rationale="Specified in the source inputs.",
                    candidates=[_c(", ".join(named), "Named in the raw requirement documents.", rec=True)]))
        if not aspects:  # a bare '<name> stack' with no per-layer tokens
            name = next((t for t in tokens if t in _STACK_NAMES), "the source-specified stack")
            aspects = [TechAspect(key="stack", title="Technology Stack",
                                  rationale="Specified in the source inputs.",
                                  candidates=[_c(name.upper(), "Named in the raw requirement documents.", rec=True)])]
        return TechStackResult(stated_in_inputs=True,
                               basis=f"Adopted the stack named in the source inputs: {', '.join(tokens) or 'as stated'}.",
                               aspects=aspects)

    blob = " ".join(r.statement.lower() for r in requirements)
    def has(sigs) -> bool:
        return any(s in blob for s in sigs)
    py_first = sum(blob.count(s) for s in _PY_SIGNALS) >= sum(blob.count(s) for s in _JS_SIGNALS)

    aspects = [
        TechAspect(key="frontend", title="Frontend / Client",
                   rationale="How users access the product across devices.",
                   candidates=[
                       _c("React", "The most widely used web UI library — huge ecosystem, easy to build with and hire for.", rec=True),
                       _c("Vue.js", "A lightweight, approachable framework for fast, simple delivery."),
                       _c("Angular", "A batteries-included framework popular in larger enterprise teams.")]),
        TechAspect(key="backend", title="Backend / API",
                   rationale="Where business logic and APIs run.",
                   candidates=[
                       _c("Node.js + Express", "Simple, ubiquitous web API framework; same language as the React client.", rec=not py_first),
                       _c("Python + FastAPI", "Fast to build, easy to read, great for data/automation workloads.", rec=py_first),
                       _c("Java + Spring Boot", "A robust, long-supported choice for larger enterprise back-ends.")]),
        TechAspect(key="database", title="Database / Persistence",
                   rationale="The system of record for domain data.",
                   candidates=[
                       _c("PostgreSQL", "The most popular open-source SQL database — reliable, JSON-capable, easy to run.", rec=True),
                       _c("MySQL", "A widely deployed relational database with broad, simple hosting support."),
                       _c("MongoDB", "A document store for flexible, denormalised data with a gentle learning curve.")]),
        TechAspect(key="auth", title="Authentication & Security",
                   rationale="How users sign in and how access is controlled.",
                   candidates=[
                       _c("JWT + bcrypt", "The standard, easy-to-build self-hosted pattern: hashed passwords and signed tokens.", rec=True),
                       _c("OAuth 2.0 / OIDC", "Standard protocol for delegated login and social sign-in."),
                       _c("Session cookies", "The simplest robust option for a single web front-end.")]),
        # --- COMMENTED OUT on request: Hosting / Infrastructure section --------------------------
        # (also excluded via EXCLUDED_ASPECT_KEYS; un-comment BOTH to restore this section)
        # TechAspect(key="hosting", title="Hosting / Infrastructure",
        #            rationale="Where the system is deployed and how it scales.",
        #            candidates=[
        #                _c("Docker", "Portable containers that run the same everywhere; the mainstream, simple default.", rec=True),
        #                _c("Managed PaaS (Render / Heroku)", "Fastest, simplest path to production for a small team."),
        #                _c("Kubernetes", "For fine-grained scaling once the platform grows (more complex).")]),
    ]
    # --- COMMENTED OUT on request: Payments section ---------------------------------------------
    # (also excluded via EXCLUDED_ASPECT_KEYS; un-comment to restore this section)
    # if has(_PAY_SIGNALS):
    #     aspects.append(TechAspect(key="payments", title="Payments",
    #         rationale="The product processes orders/payments.",
    #         candidates=[
    #             _c("Stripe (test mode)", "The most popular payments API with strong SDKs; test mode builds checkout end-to-end without live keys.", rec=True),
    #             _c("Razorpay", "A popular, easy-to-integrate gateway for India-focused GST/UPI commerce."),
    #             _c("PayPal", "A widely recognised, simple-to-add checkout option.")]))
    if has(_SEARCH_SIGNALS):
        aspects.append(TechAspect(key="search", title="Search & Discovery",
            rationale="Users browse and search a catalogue.",
            candidates=[
                _c("PostgreSQL full-text search", "Reuses the primary database for search/filters — simplest for a moderate catalogue.", rec=True),
                _c("Elasticsearch", "Scales to rich faceted search over large catalogues."),
                _c("Algolia", "A managed search service with fast relevance out of the box.")]))
    if has(_NOTIFY_SIGNALS):
        aspects.append(TechAspect(key="notifications", title="Notifications / Messaging",
            rationale="The product sends order/account messages.",
            candidates=[
                _c("SendGrid + Twilio", "Simple, reliable email/SMS APIs with templated delivery and receipts.", rec=True),
                _c("AWS SES / SNS", "Cloud-native email/SMS that fits an AWS deployment."),
                _c("Firebase Cloud Messaging", "An easy option for in-app/push notifications.")]))

    basis = ("No stack is fixed in the inputs; candidates are mainstream defaults, and the recommended "
             "backend leans " + ("Python (analytical/automation signals)" if py_first else
             "a JS/TypeScript stack (web/real-time signals)") + ".")
    return TechStackResult(stated_in_inputs=False, basis=basis, aspects=aspects)


def _normalise(result: TechStackResult, stated: bool) -> TechStackResult:
    """Guarantee the invariants the renderer/UI rely on: every aspect has a stable key and EXACTLY one
    recommended candidate; adopted aspects keep a single candidate."""
    clean: list[TechAspect] = []
    seen_keys: set[str] = set()
    for i, a in enumerate(result.aspects or []):
        cands = list(a.candidates or [])
        if not cands:
            continue
        if stated:
            cands = cands[:1]
            cands[0].recommended = True
        else:
            cands = cands[:3]
            if not any(c.recommended for c in cands):
                cands[0].recommended = True
            seen_rec = False
            for c in cands:
                if c.recommended and seen_rec:
                    c.recommended = False
                seen_rec = seen_rec or c.recommended
        key = (a.key or "").strip().lower() or f"aspect-{i + 1}"
        while key in seen_keys:
            key += "_"
        seen_keys.add(key)
        a.key, a.candidates = key, cands
        clean.append(a)
    result.aspects = clean
    result.stated_in_inputs = stated
    return result


# --- switched-off aspects ----------------------------------------------------
# Aspects deliberately COMMENTED OUT / removed on request — dropped from the review screen AND SRS §7,
# whichever path (LLM or fallback) produced them. To RESTORE a section, remove its key/word from these
# sets (and un-comment its fallback block in `_fallback` below). Keys cover the common variants a model
# might emit; the title-word scan catches an aspect that uses an unexpected key.
EXCLUDED_ASPECT_KEYS = frozenset(
    {"payments", "payment", "hosting", "infra", "infrastructure", "deployment", "devops"}
)
_EXCLUDED_TITLE_WORDS = ("payment", "hosting", "infrastructure")


def is_excluded_aspect(key: str, title: str = "") -> bool:
    """True if an aspect has been switched off (Payments, Hosting / Infrastructure)."""
    k = (key or "").strip().lower()
    t = (title or "").lower()
    return k in EXCLUDED_ASPECT_KEYS or any(w in t for w in _EXCLUDED_TITLE_WORDS)


def _apply_exclusions(result: TechStackResult) -> TechStackResult:
    """Drop the switched-off aspects from a result (any path), so they never reach the review
    screen or SRS §7."""
    result.aspects = [a for a in (result.aspects or []) if not is_excluded_aspect(a.key, a.title)]
    return result


def analyze_tech_stack(
    provider: LLMProvider | None,
    requirements: list[Requirement],
    chunks: list[Chunk],
    *,
    project_name: str = "the system",
    run_llm: bool = True,
) -> TechStackResult:
    """Decide/propose the technology stack for SRS §7 as per-aspect candidates. Deterministic when no
    provider is given. Switched-off aspects (Payments, Hosting / Infrastructure) are excluded from
    every path via `_apply_exclusions`."""
    text = _corpus_text(chunks)
    tokens = detect_stated_tokens(text)
    stated = _is_stated(text, tokens)

    if not run_llm or provider is None or not requirements:
        return _apply_exclusions(_fallback(project_name, requirements, tokens, stated))

    system = _STATED_SYSTEM if stated else _PROPOSE_SYSTEM
    user = _digest(project_name, requirements, tokens) + (
        "\n\nThe sources DO name a stack — adopt it, one candidate per aspect." if stated
        else "\n\nThe sources do NOT fix a stack — propose 2-3 SIMPLE, widely-used, easy-to-build "
             "candidates per aspect (mainstream building blocks a team can implement directly; no "
             "all-in-one platforms or managed SaaS that replace the build) and recommend the simplest "
             "option that meets this product's requirements.")
    try:
        result = provider.structured(system, user, TechStackResult,
                                     max_tokens=MAX_TOKENS, timeout_s=TIMEOUT_S)
    except Exception:
        return _apply_exclusions(_fallback(project_name, requirements, tokens, stated))
    if not result.aspects:                     # model returned nothing usable -> safe default
        return _apply_exclusions(_fallback(project_name, requirements, tokens, stated))
    return _apply_exclusions(_normalise(result, stated))

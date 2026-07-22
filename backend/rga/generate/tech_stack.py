"""SRS §7 Technology Stack renderer.

The tech-stack agent proposes per-ASPECT candidates (frontend, backend, database, auth, hosting,
payments, search, …) and the reviewer picks one per aspect. This renderer maps those per-aspect
picks into QuickBite's six §7 LAYERS — 7.1 Client Applications · 7.2 Backend Architecture ·
7.3 Data Storage · 7.4 Third-Party Integrations · 7.5 Security Technologies · 7.6 Device Capabilities
— so the SRS structure matches the reference while the per-aspect review model is preserved. Never
hand-written; the generator rebuilds §7 from the run analysis + the review selections.
"""

from __future__ import annotations

from ..agents.techstack import TechAspect, TechCandidate, TechStackResult, is_excluded_aspect
from .common import DEFERRED, md_line

# The six reference layers, in order: (number, title, bucket-key).
_LAYERS: list[tuple[str, str, str]] = [
    ("7.1", "Client Applications", "client"),
    ("7.2", "Backend Architecture", "backend"),
    ("7.3", "Data Storage", "data"),
    ("7.4", "Third-Party Integrations", "integration"),
    ("7.5", "Security Technologies", "security"),
    ("7.6", "Device Capabilities", "device"),
]
# aspect key -> layer bucket (exact); unknown keys fall through to the keyword scan below.
_ASPECT_LAYER: dict[str, str] = {
    "frontend": "client", "client": "client", "mobile": "client", "ui": "client", "web": "client",
    "backend": "backend", "api": "backend", "hosting": "backend", "infra": "backend",
    "infrastructure": "backend", "runtime": "backend", "server": "backend", "deployment": "backend",
    "database": "data", "data": "data", "persistence": "data", "storage": "data", "cache": "data",
    "media": "data", "files": "data",
    "payments": "integration", "payment": "integration", "notifications": "integration",
    "notification": "integration", "messaging": "integration", "search": "integration",
    "analytics": "integration", "email": "integration", "sms": "integration",
    "auth": "security", "security": "security", "authentication": "security", "authorization": "security",
    "device": "device", "devices": "device",
}
_KEYWORD_LAYER: tuple[tuple[str, str], ...] = (
    ("front", "client"), ("client", "client"), ("mobile", "client"), ("browser", "client"),
    ("back", "backend"), ("api", "backend"), ("host", "backend"), ("infra", "backend"),
    ("deploy", "backend"), ("server", "backend"),
    ("data", "data"), ("db", "data"), ("storage", "data"), ("persist", "data"), ("cache", "data"),
    ("media", "data"), ("file", "data"),
    ("auth", "security"), ("secur", "security"),
    ("pay", "integration"), ("notif", "integration"), ("search", "integration"),
    ("messag", "integration"), ("analytic", "integration"), ("email", "integration"),
    ("device", "device"),
)
_DEVICE_DEFAULT = (
    "The client experiences are responsive web applications, so no native device capabilities "
    "(camera, GPS, push, biometrics) are required for this release beyond standard browser APIs. "
    "Native-app device features are out of scope until dedicated mobile apps are introduced."
)
_EMPTY_LAYER = "Not specified by the requirements for this release; to be determined in Design."


def _coerce(result) -> TechStackResult | None:
    if result is None:
        return None
    if isinstance(result, TechStackResult):
        return result
    try:
        return TechStackResult.model_validate(result)   # stored JSON dict -> typed model
    except Exception:
        return None


def _chosen(aspect: TechAspect, selection: dict) -> TechCandidate | None:
    """The reviewer-selected candidate for this aspect, else the recommended one, else the first.

    If the selection is a value NOT among the proposed candidates, it is a reviewer's own "Other"
    entry — render it as its own candidate (so a custom technology reaches §7 instead of being
    silently replaced by the recommended default)."""
    sel = (selection or {}).get(aspect.key)
    if sel and sel.strip():
        for c in aspect.candidates:
            if c.name.strip().lower() == sel.strip().lower():
                return c
        return TechCandidate(name=sel.strip(), recommended=False,
                             reason="Specified by the reviewer (custom entry).")
    return next((c for c in aspect.candidates if c.recommended),
                aspect.candidates[0] if aspect.candidates else None)


def _layer_of(key: str) -> str:
    k = (key or "").lower()
    if k in _ASPECT_LAYER:
        return _ASPECT_LAYER[k]
    for kw, layer in _KEYWORD_LAYER:
        if kw in k:
            return layer
    return "integration"    # unknown external concern defaults to a third-party integration


def tech_stack_markdown(result, selection: dict | None = None) -> str:
    """Render the §7 body as the six reference layers. `selection` maps aspect key -> chosen name."""
    res = _coerce(result)
    if res is None or not res.aspects:
        return ("The technology stack is produced by the requirement-analysis run. Re-run the pipeline "
                "to populate this section.\n\n" + DEFERRED)
    # Drop switched-off aspects (Payments, Hosting / Infrastructure) even from stored/older run data,
    # so a regenerate removes them from §7 without needing a fresh tech-stack analysis.
    res.aspects = [a for a in res.aspects if not is_excluded_aspect(a.key, a.title)]
    selection = selection or {}
    chosen = [(a, _chosen(a, selection)) for a in res.aspects]
    chosen = [(a, c) for a, c in chosen if c is not None]
    by_layer: dict[str, list[tuple[TechAspect, TechCandidate]]] = {}
    for a, c in chosen:
        by_layer.setdefault(_layer_of(a.key), []).append((a, c))
    summary = " · ".join(c.name for _a, c in chosen)

    if res.stated_in_inputs:
        lead = ("The technology stack below is taken directly from the project's source inputs"
                + (f" ({md_line(res.basis)})" if res.basis else "") + " and is not a recommendation.")
    else:
        lead = ("*Proposed technology stack — generated from the requirements; to be confirmed by "
                "Design / Engineering.* Each layer names the option selected on the review screen (the "
                "recommended default unless a reviewer changed it).")
        if summary:
            lead += f"\n\n**Selected stack:** {summary}."

    out = [lead]
    for num, title, bucket in _LAYERS:
        out.append(f"### {num} {title}")
        items = by_layer.get(bucket, [])
        if items:
            lines = []
            for a, c in items:
                detail = " ".join(x for x in (md_line(a.rationale) if a.rationale else "",
                                              md_line(c.reason) if c.reason else "") if x).strip()
                lines.append(f"**{c.name}** — {detail}" if detail else f"**{c.name}**")
            out.append("  \n".join(lines))
        else:
            out.append(_DEVICE_DEFAULT if bucket == "device" else _EMPTY_LAYER)
    return "\n\n".join(p for p in out if p).strip() + "\n"

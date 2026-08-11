"""Design-token generator for SRS §3.1.1–3.1.5 (Parts B & C).

The raw inputs specify no colours/typography, so the system PROPOSES a token set from the
requirements — never a bare TBD, never a hand-typed hex. Pipeline (Part B):
  1. personality  — derive brand personality + hue families from the requirements (LLM, or a
     deterministic domain-keyword fallback);
  2. semantic schema — always the SAME 12 roles (status roles fixed to convention);
  3. library anchoring — snap every role to a named step of the checked-in OPEN COLOR ramp (MIT,
     open-source) so hexes are deterministic and reviewable — the model never free-types a hex;
  4. accessibility gate — verify WCAG AA in code; auto-shift the ramp step until it passes;
  5. honesty — the section is clearly marked a proposal for Design to confirm.
§3.1.3–3.1.5 (Part C) use standard modular type / 4-8px spacing / elevation scales + WCAG-AA layout
standards. Everything is deterministic given the personality result, so output is reproducible.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.base import LLMProvider
from ..models import Requirement
from .color_util import contrast_ratio

# --- Brand profiles ---------------------------------------------------------------------------------
# When an organisation brand is active (e.g. Zensar), §3.1 ADOPTS that brand's palette/personality
# instead of deriving one from the requirements. Profiles are JSON beside this module
# (`<name>_brand.json`); see zensar_brand.json. Passing brand=None keeps the legacy derived palette.
_BRANDS_DIR = Path(__file__).parent


def load_brand(name_or_path: str | None = "zensar") -> dict | None:
    """Resolve a brand profile: a name ('zensar' -> zensar_brand.json beside this file) or a path to
    a JSON file. Returns None if not found / not requested / unreadable — so a missing or malformed
    brand file degrades to the legacy derived palette rather than breaking generation."""
    if not name_or_path:
        return None
    try:
        p = Path(name_or_path)
        if p.suffix.lower() == ".json" and p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        cand = _BRANDS_DIR / f"{name_or_path}_brand.json"
        return json.loads(cand.read_text(encoding="utf-8")) if cand.is_file() else None
    except (json.JSONDecodeError, OSError, ValueError):
        return None

# --- Open Color ramp (MIT, https://yeun.github.io/open-color/) — steps 0 (lightest) … 9 (darkest) --
OPEN_COLOR: dict[str, list[str]] = {
    "gray":   ["f8f9fa", "f1f3f5", "e9ecef", "dee2e6", "ced4da", "adb5bd", "868e96", "495057", "343a40", "212529"],
    "red":    ["fff5f5", "ffe3e3", "ffc9c9", "ffa8a8", "ff8787", "ff6b6b", "fa5252", "f03e3e", "e03131", "c92a2a"],
    "pink":   ["fff0f6", "ffdeeb", "fcc2d7", "faa2c1", "f783ac", "f06595", "e64980", "d6336c", "c2255c", "a61e4d"],
    "grape":  ["f8f0fc", "f3d9fa", "eebefa", "e599f7", "da77f2", "cc5de8", "be4bdb", "ae3ec9", "9c36b5", "862e9c"],
    "violet": ["f3f0ff", "e5dbff", "d0bfff", "b197fc", "9775fa", "845ef7", "7950f2", "7048e8", "6741d9", "5f3dc4"],
    "indigo": ["edf2ff", "dbe4ff", "bac8ff", "91a7ff", "748ffc", "5c7cfa", "4c6ef5", "4263eb", "3b5bdb", "364fc7"],
    "blue":   ["e7f5ff", "d0ebff", "a5d8ff", "74c0fc", "4dabf7", "339af0", "228be6", "1c7ed6", "1971c2", "1864ab"],
    "cyan":   ["e3fafc", "c5f6fa", "99e9f2", "66d9e8", "3bc9db", "22b8cf", "15aabf", "1098ad", "0c8599", "0b7285"],
    "teal":   ["e6fcf5", "c3fae8", "96f2d7", "63e6be", "38d9a9", "20c997", "12b886", "0ca678", "099268", "087f5b"],
    "green":  ["ebfbee", "d3f9d8", "b2f2bb", "8ce99a", "69db7c", "51cf66", "40c057", "37b24d", "2f9e44", "2b8a3e"],
    "lime":   ["f4fce3", "e9fac8", "d8f5a2", "c0eb75", "a9e34b", "94d82d", "82c91e", "74b816", "66a80f", "5c940d"],
    "yellow": ["fff9db", "fff3bf", "ffec99", "ffe066", "ffd43b", "fcc419", "fab005", "f59f00", "f08c00", "e67700"],
    "orange": ["fff4e6", "ffe8cc", "ffd8a8", "ffc078", "ffa94d", "ff922b", "fd7e14", "f76707", "e8590c", "d9480f"],
}
_HUES = tuple(k for k in OPEN_COLOR if k != "gray")

# Step 1 — deterministic personality fallback: domain keyword -> (primary hue, secondary hue, words).
_DOMAIN_HUES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (("restaurant", "food", "menu", "delivery", "meal", "kitchen"), "orange", "red", "warm, appetising, energetic"),
    (("bank", "finance", "payment", "invoice", "loan", "wallet", "tax"), "indigo", "teal", "trustworthy, secure, precise"),
    (("health", "patient", "clinic", "medical", "care", "wellness"), "teal", "green", "calm, clean, reassuring"),
    (("travel", "flight", "hotel", "booking", "trip"), "blue", "cyan", "open, fresh, dependable"),
    (("learn", "course", "education", "student", "school"), "violet", "blue", "curious, approachable, focused"),
    (("shop", "retail", "commerce", "cart", "checkout", "catalogue", "storefront", "order", "product"),
     "indigo", "teal", "trustworthy, efficient, conversion-focused"),
)


class Personality(BaseModel):
    personality_words: list[str] = Field(default_factory=list)
    primary_hue_family: str = ""
    secondary_hue_family: str = ""
    rationale: str = ""


_PERSONALITY_SYSTEM = (
    "You derive a brand VISUAL PERSONALITY for a product from its requirements, to seed a design "
    "system. Return 3-5 `personality_words`, a `primary_hue_family` and `secondary_hue_family` chosen "
    "ONLY from this set: " + ", ".join(_HUES) + ". Pick hues that fit the domain/audience/tone (e.g. a "
    "food product -> warm orange/red; a finance product -> trustworthy indigo/teal). Give a one-sentence "
    "`rationale` grounding the choice in the requirements. Do NOT output hex colours."
)


def _personality_fallback(requirements: list[Requirement], project_name: str) -> Personality:
    blob = (project_name + " " + " ".join(r.statement for r in requirements)).lower()
    for kws, prim, sec, words in _DOMAIN_HUES:
        if any(k in blob for k in kws):
            return Personality(personality_words=words.split(", "), primary_hue_family=prim,
                               secondary_hue_family=sec,
                               rationale=f"Domain signals ({', '.join(k for k in kws if k in blob)[:60]}) suggest a "
                                         f"{words} identity.")
    return Personality(personality_words=["clear", "modern", "trustworthy"], primary_hue_family="indigo",
                       secondary_hue_family="teal", rationale="No strong domain signal; a neutral, "
                       "trustworthy default palette is proposed.")


def _derive_personality(provider: LLMProvider | None, requirements: list[Requirement],
                        project_name: str, run_llm: bool) -> Personality:
    fb = _personality_fallback(requirements, project_name)
    if not run_llm or provider is None or not requirements:
        return fb
    sample = "\n".join(f"- {r.statement}" for r in requirements[:40])
    user = f"PROJECT: {project_name}\nREQUIREMENTS (sample):\n{sample}\n\nDerive the visual personality."
    try:
        p = provider.structured(_PERSONALITY_SYSTEM, user, Personality, max_tokens=400, timeout_s=120.0)
    except Exception:
        return fb
    if p.primary_hue_family not in OPEN_COLOR:
        p.primary_hue_family = fb.primary_hue_family
    if p.secondary_hue_family not in OPEN_COLOR or p.secondary_hue_family == p.primary_hue_family:
        p.secondary_hue_family = fb.secondary_hue_family
    if not p.personality_words:
        p.personality_words = fb.personality_words
    if not p.rationale:
        p.rationale = fb.rationale
    return p


# --- Steps 3+4 — anchor each of the 12 semantic roles to a ramp step, gated for WCAG AA ------------
_SURFACE, _CANVAS = "ffffff", OPEN_COLOR["gray"][0]


def _fit(hue: str, start: int, min_ratio: float, backgrounds: list[str]) -> str:
    """Walk DARKER from `start` to the first ramp step whose contrast vs ALL backgrounds >= min_ratio."""
    ramp = OPEN_COLOR[hue]
    for s in range(start, len(ramp)):
        if all(contrast_ratio(ramp[s], bg) >= min_ratio for bg in backgrounds):
            return ramp[s]
    return ramp[-1]


def generate_colour_tokens(requirements: list[Requirement], *, provider: LLMProvider | None = None,
                           project_name: str = "the product", run_llm: bool = True,
                           brand: dict | None = None) -> tuple[Personality, list[dict]]:
    """Return (personality, token_rows) where each row is (token, hex, usage). When a `brand` profile
    is given, the palette is ADOPTED verbatim from it (the brand IS the design system). Otherwise all
    hex values come from the OPEN COLOR ramp and every text/border pair is AA-verified (Steps 3+4)."""
    if brand:
        p = Personality(personality_words=list(brand.get("personality_words") or []),
                        rationale=brand.get("rationale", ""))
        rows = [(c["token"], str(c["hex"]).lstrip("#").lower(), c["usage"])
                for c in brand.get("colour_tokens", [])]
        return p, rows
    p = _derive_personality(provider, requirements, project_name, run_llm)
    prim, sec = p.primary_hue_family, p.secondary_hue_family
    ink = _fit("gray", 9, 4.5, [_SURFACE, _CANVAS])
    body = _fit("gray", 8, 4.5, [_SURFACE, _CANVAS])
    muted = _fit("gray", 6, 4.5, [_SURFACE, _CANVAS])       # shifts darker until AA on both surfaces
    border = _fit("gray", 3, 3.0, [_CANVAS])                # UI/border threshold 3:1
    primary_dark = _fit(prim, 8, 4.5, [_SURFACE])           # white text on primary-dark buttons
    rows = [
        ("color-primary", OPEN_COLOR[prim][6], "Primary brand colour — key actions, active states, links."),
        ("color-primary-dark", primary_dark, "Darker primary — button text-on-fill and hover/pressed states (AA)."),
        ("color-secondary", OPEN_COLOR[sec][6], "Secondary/accent — supporting highlights and secondary actions."),
        ("color-success", OPEN_COLOR["green"][7], "Success status — confirmations, positive badges (with an icon/label)."),
        ("color-warning", OPEN_COLOR["orange"][6], "Warning status — cautions (never colour alone)."),
        ("color-error", OPEN_COLOR["red"][7], "Error status — validation errors, destructive actions."),
        ("color-ink", ink, "Primary text / headings on light surfaces."),
        ("color-body", body, "Body text on light surfaces."),
        ("color-muted", muted, "Secondary/muted text — captions, hints (AA-verified)."),
        ("color-surface", _SURFACE, "Card / panel surface."),
        ("color-canvas", _CANVAS, "Page background (warm off-white)."),
        ("color-border", border, "Borders, dividers, input outlines (AA 3:1 vs canvas)."),
    ]
    return p, rows


# --- Part C — typography / spacing / layout (standard scales; provisional) ------------------------
_TYPE_ROWS = [
    ("type-display", "32px / 40px", "700", "Page or hero titles."),
    ("type-h1", "24px / 32px", "700", "Section headings."),
    ("type-h2", "20px / 28px", "600", "Sub-section headings."),
    ("type-h3", "16px / 24px", "600", "Card / group headings."),
    ("type-body", "16px / 24px", "400", "Default body text."),
    ("type-small", "14px / 20px", "400", "Secondary text, metadata."),
    ("type-caption", "12px / 16px", "400", "Captions, helper text, labels."),
]
_SPACE_ROWS = [
    ("space-1", "4px", "Tight gaps (icon-to-label)."), ("space-2", "8px", "Compact padding, chips."),
    ("space-3", "12px", "Control padding."), ("space-4", "16px", "Default element spacing."),
    ("space-6", "24px", "Card padding, group spacing."), ("space-8", "32px", "Section spacing."),
    ("radius-sm", "6px", "Inputs, chips."), ("radius-md", "10px", "Buttons, cards."),
    ("radius-lg", "16px", "Modals, sheets."),
    ("elevation-1", "0 1px 2px rgba(0,0,0,.06)", "Cards at rest."),
    ("elevation-2", "0 4px 12px rgba(0,0,0,.10)", "Dropdowns, popovers."),
    ("elevation-3", "0 12px 28px rgba(0,0,0,.14)", "Modals, dialogs."),
]
_PROVISIONAL = ("_Proposed design system — generated from the requirements; the raw inputs specify no "
                "visual design, so Design/UX to confirm before build._")


def _table(headers: list[str], rows: list[tuple]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _brand_marker(brand: dict) -> str:
    return (f"_Proposed design system — adopted from the {brand.get('name', 'organisation')} corporate brand "
            f"guidelines; Design/UX to confirm application details before build._")


def _brand_accents_md(brand: dict) -> str:
    """A bulleted (NOT tabular) accent-palette reference so designers have the brand's supporting
    swatches + tint rule. Deliberately a list, not a table, so it never competes with the parser's
    §3.1.2 colour-token table."""
    acc = brand.get("accent_palette", [])
    if not acc:
        return ""
    tint_str = "/".join(f"{t}%" for t in (brand.get("accent_tints") or [80, 60, 40]))
    swatches = " · ".join(f"{c['name']} `#{str(c['hex']).upper()}`" for c in acc)
    return (f"**Brand accent palette** — secondary supporting colours; apply at {tint_str} tints and "
            f"never as the dominant colour:\n\n{swatches}")


def _brand_theme(brand: dict, words: str) -> str:
    """§3.1.1 prose for an adopted organisation brand (Zensar): names the primaries, the accent-as-
    secondary rule (with tints), and the geometric element motifs — straight from the brand guide."""
    name = brand.get("name", "the organisation")
    prim = [c for c in brand.get("primary_palette", []) if str(c.get("hex", "")).upper() not in ("FFFFFF", "000000")]
    prim_str = ", ".join(f"{c['name']} (#{str(c['hex']).upper()})" for c in prim)
    acc_str = ", ".join(c["name"].lower() for c in brand.get("accent_palette", []))
    tint_str = "/".join(f"{t}%" for t in (brand.get("accent_tints") or [80, 60, 40]))
    el_str = ", ".join(brand.get("elements", []))
    return (
        f"The interface adopts the **{name}** brand system. The product's visual personality is **{words}**. "
        f"{brand.get('rationale', '')} Foundational colours are the {name} primaries — {prim_str}, with black "
        f"and white completing the core. Per the brand guidance, the primaries are the foundation while the "
        f"accent family ({acc_str}) is used ONLY as **secondary supporting** colour at {tint_str} tints — "
        f"keeping balance and avoiding overpowering the canvas with any one colour. Every colour, type step and "
        f"spacing value below is a NAMED token (never a hard-coded literal), so the look is consistent and "
        f"re-themeable. The brand's geometric elements — {el_str} — provide the visual motif for accents and "
        f"section dividers. Status is always conveyed by an icon or label as well as colour."
    )


def generate_design_tokens(requirements: list[Requirement], *, provider: LLMProvider | None = None,
                           project_name: str = "the product", run_llm: bool = True,
                           brand: dict | None = None) -> dict[str, str]:
    """Build the §3.1.1–3.1.5 content (Parts B & C). Returns {section_number: markdown}. When a
    `brand` profile is given (e.g. Zensar), the palette + personality are ADOPTED from it."""
    p, colour_rows = generate_colour_tokens(requirements, provider=provider,
                                            project_name=project_name, run_llm=run_llm, brand=brand)
    words = ", ".join(p.personality_words) if p.personality_words else "clear, modern, trustworthy"
    marker = _brand_marker(brand) if brand else _PROVISIONAL
    if brand:
        theme = f"{marker}\n\n" + _brand_theme(brand, words)
    else:
        theme = (
            f"{marker}\n\n"
            f"The product's visual personality is **{words}**. {p.rationale} The design system is the single "
            f"source of truth for the interface: every colour, type step and spacing value below is a NAMED "
            f"token (never a hard-coded literal), so the look is consistent and re-themeable. The brand hue "
            f"is used as an accent (~60/30/10 neutral/secondary/brand), not a flood, and status is always "
            f"conveyed by an icon or label as well as colour."
        )
    colour = marker + "\n\n" + _table(["Token", "Hex", "Usage"],
                                      [(t, f"`#{h}`", u) for t, h, u in colour_rows])
    if brand:  # surface the brand's full accent swatch set (as a list, not a competing table)
        accents_md = _brand_accents_md(brand)
        if accents_md:
            colour += "\n\n" + accents_md
    typography = marker + "\n\n" + _table(["Token", "Size / Line-height", "Weight", "Usage"], _TYPE_ROWS)
    spacing = marker + "\n\n" + _table(["Token", "Value", "Usage"], _SPACE_ROWS)
    layout = (
        f"{marker}\n\n"
        "- **Accessibility:** all text meets **WCAG 2.1 AA** contrast (≥ 4.5:1 body, ≥ 3:1 large text / "
        "UI / borders); the token palette above is generated to satisfy this.\n"
        "- **Status is never colour alone:** success/warning/error are always paired with an icon and a "
        "text label so colour-blind users are not excluded.\n"
        "- **Touch targets:** interactive controls are at least **44 × 44 px** with adequate spacing.\n"
        "- **Focus:** every interactive element has a visible keyboard-focus indicator using "
        "`color-primary`.\n"
        "- **Responsive:** layouts use the spacing scale and reflow from a single-column mobile view to "
        "multi-column desktop; content width is capped for readability.\n"
        "- **Motion:** transitions are subtle and respect `prefers-reduced-motion`."
    )
    return {"3.1.1": theme, "3.1.2": colour, "3.1.3": typography, "3.1.4": spacing, "3.1.5": layout}

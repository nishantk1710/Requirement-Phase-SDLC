"""Zensar brand adoption for the SRS §3.1 design tokens.

Org feedback: the SRS UI section (colour tokens et al.) must be based on the Zensar brand elements.
The generation path now ADOPTS the Zensar palette/personality by default, while the low-level
derived-palette path (no brand) is unchanged. All AA guarantees are preserved.
"""
from __future__ import annotations

from rga.generate.color_util import contrast_ratio
from rga.generate.design_tokens import generate_colour_tokens, generate_design_tokens, load_brand
from rga.generate.handoff import generate_handoff
from rga.models import Priority, Requirement, RType, SourceRef, Status

ZBRAND = load_brand("zensar")


def _r(rid, s, t, **k):
    return Requirement(id=rid, project_id="Z", statement=s, rtype=RType(t), feature=k.get("f"),
                       nfr_category=k.get("n"), priority=Priority(k["p"]) if k.get("p") else None,
                       status=Status.approved,
                       source_refs=[SourceRef(doc_id="brd", source_type="brd", location="1",
                                              raw_quote=s, start=0, end=len(s))])


def _reqs():
    return [_r("f1", "The system shall let a customer browse the catalogue.", "functional", f="Catalogue", p="must"),
            _r("f2", "The system shall let a shopper add items to the cart.", "functional", f="Cart & Checkout"),
            _r("n1", "Pages shall respond within 2 seconds.", "non_functional", n="performance"),
            _r("b1", "Prices must be tax-inclusive.", "business")]


def test_brand_loads():
    assert ZBRAND and ZBRAND["name"] == "Zensar"


def test_brand_colour_tokens_are_the_zensar_palette():
    _p, rows = generate_colour_tokens(_reqs(), brand=ZBRAND)
    d = {t: h for t, h, _u in rows}
    assert d["color-primary"] == "3956a5"        # Royal blue
    assert d["color-primary-dark"] == "10005d"   # Navy blue
    assert d["color-secondary"] == "ff4b41"      # Scarlet red
    assert d["color-error"] == "b41e26"          # Crimson red


def test_brand_palette_still_meets_wcag_aa():
    _p, rows = generate_colour_tokens(_reqs(), brand=ZBRAND)
    d = {t: h for t, h, _u in rows}
    for role in ("color-ink", "color-body", "color-muted"):
        assert contrast_ratio(d[role], d["color-surface"]) >= 4.5, f"{role}/surface"
        assert contrast_ratio(d[role], d["color-canvas"]) >= 4.5, f"{role}/canvas"
    assert contrast_ratio(d["color-border"], d["color-canvas"]) >= 3.0        # border/UI
    assert contrast_ratio("ffffff", d["color-primary-dark"]) >= 4.5           # white on navy
    assert contrast_ratio("ffffff", d["color-primary"]) >= 4.5                # white on royal blue
    assert contrast_ratio("ffffff", d["color-error"]) >= 4.5                  # white on crimson


def test_brand_theme_names_the_palette_and_elements():
    d = generate_design_tokens(_reqs(), brand=ZBRAND)
    assert set(d) == {"3.1.1", "3.1.2", "3.1.3", "3.1.4", "3.1.5"}
    theme = d["3.1.1"]
    assert "Zensar" in theme and "personality" in theme.lower()
    assert "Royal blue" in theme and "quarter-circle" in theme                # brand colours + elements
    assert "3956a5" in d["3.1.2"].lower()                                     # brand hex in the colour table
    for md in d.values():
        assert "Proposed" in md                                              # honesty marker preserved


def test_brand_accent_palette_is_surfaced_without_a_second_table():
    colour = generate_design_tokens(_reqs(), brand=ZBRAND)["3.1.2"]
    assert "Brand accent palette" in colour
    for hexv in ("87C8C3", "B7A2E0", "EAADB8", "97AFD4"):        # teal, lavender, pink, light-blue
        assert hexv in colour.upper(), hexv
    # exactly ONE colour-token table — the accent list must NOT add a competing table (which would
    # overwrite the design parser's ui_tokens['colour']).
    assert colour.count("| Token | Hex | Usage |") == 1


def test_generate_handoff_adopts_zensar_by_default_and_stays_valid():
    pack = generate_handoff(_reqs(), project_name="Zensar Shop", date="2026-01-01")  # brand defaults to zensar
    assert pack["manifest"]["design_brand"] == "Zensar"
    assert "3956a5" in pack["srs_markdown"].lower()                          # Zensar primary reached the SRS
    fv = pack["manifest"]["format_validation"]
    assert fv["ok"], fv["summary"]                                           # still passes the parser format schema


def test_legacy_derived_palette_unchanged_without_a_brand():
    _p, rows = generate_colour_tokens(_reqs())                               # brand=None -> OPEN COLOR ramp
    d = {t: h for t, h, _u in rows}
    assert d["color-primary"] != "3956a5"                                    # not the Zensar hex

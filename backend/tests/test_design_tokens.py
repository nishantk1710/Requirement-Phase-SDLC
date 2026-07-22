"""Parts B & C — design-token generator: 12 semantic colour roles, every hex snapped to the
open-source ramp, WCAG-AA gated, provisional-marked; typography/spacing/layout sections."""

from __future__ import annotations

from rga.generate.color_util import contrast_ratio
from rga.generate.design_tokens import OPEN_COLOR, generate_colour_tokens, generate_design_tokens
from rga.generate.srs import generate_srs
from rga.models import Requirement, RType, SourceRef, Status

_EXPECTED_ROLES = {
    "color-primary", "color-primary-dark", "color-secondary", "color-success", "color-warning",
    "color-error", "color-ink", "color-body", "color-muted", "color-surface", "color-canvas",
    "color-border",
}


def _r(s: str, approved: bool = False) -> Requirement:
    r = Requirement(id="x", project_id="P", statement=s, rtype=RType.functional,
                    source_refs=[SourceRef(doc_id="d", source_type="brd", location="1",
                                           raw_quote=s, start=0, end=1)])
    if approved:
        r.status = Status.approved
    return r


REQS = [_r("The system shall let a customer add to cart and checkout in a retail storefront."),
        _r("The system shall process card payments and issue a GST invoice.")]


def test_twelve_semantic_roles_present():
    _p, rows = generate_colour_tokens(REQS)
    assert {t for t, _h, _u in rows} == _EXPECTED_ROLES


def test_every_hex_is_snapped_to_the_open_color_ramp():
    ramp = {h for hexes in OPEN_COLOR.values() for h in hexes} | {"ffffff"}
    _p, rows = generate_colour_tokens(REQS)
    for token, hexv, _u in rows:
        assert hexv in ramp, f"{token}={hexv} is not a ramp value (no free-typed hex)"


def test_text_and_border_pairs_meet_wcag_aa():
    _p, rows = generate_colour_tokens(REQS)
    d = {t: h for t, h, _u in rows}
    for role in ("color-ink", "color-body", "color-muted"):
        assert contrast_ratio(d[role], d["color-surface"]) >= 4.5, f"{role}/surface"
        assert contrast_ratio(d[role], d["color-canvas"]) >= 4.5, f"{role}/canvas"
    assert contrast_ratio(d["color-border"], d["color-canvas"]) >= 3.0        # border/UI
    assert contrast_ratio("ffffff", d["color-primary-dark"]) >= 4.5           # white text on primary-dark


def test_personality_is_domain_driven_and_deterministic():
    p1 = generate_colour_tokens(REQS)[0]
    p2 = generate_colour_tokens(REQS)[0]
    assert p1.primary_hue_family in OPEN_COLOR and p1.secondary_hue_family in OPEN_COLOR
    assert p1.primary_hue_family == p2.primary_hue_family                     # deterministic
    food = generate_colour_tokens([_r("Users order food from a restaurant menu for delivery.")])[0]
    assert food.primary_hue_family in ("orange", "red")                       # domain -> warm


def test_design_token_sections_and_provisional_marker():
    d = generate_design_tokens(REQS)
    assert set(d) == {"3.1.1", "3.1.2", "3.1.3", "3.1.4", "3.1.5"}
    for md in d.values():
        assert "Proposed" in md                                              # honesty marker (Step 5)
    assert "| Token | Hex | Usage |" in d["3.1.2"]                           # colour table
    assert "Size / Line-height" in d["3.1.3"] and "space-4" in d["3.1.4"]
    assert "WCAG" in d["3.1.5"] and "44" in d["3.1.5"]                       # AA + 44px touch target


def test_srs_renders_populated_design_sections_not_deferred():
    reqs = [_r("The system shall let a customer add to cart and checkout.", approved=True)]
    d = generate_design_tokens(reqs)
    md = generate_srs(reqs, project_name="Shop", date="2026-01-01", design_tokens=d)
    theme = md.split("#### 3.1.1", 1)[1].split("####", 1)[0]
    assert "Deferred" not in theme and "personality" in theme.lower()
    assert "| Token | Hex | Usage |" in md                                   # §3.1.2 table in the SRS

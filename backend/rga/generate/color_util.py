"""WCAG colour-contrast utilities — shared by the docx styling (Part K) and the design-token
colour pipeline (Part B). Pure, deterministic, no dependencies."""

from __future__ import annotations


def _rel_luminance(hex_color: str) -> float:
    """WCAG 2.1 relative luminance of an sRGB hex colour ('#rrggbb' or 'rrggbb')."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected a 6-digit hex colour, got {hex_color!r}")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """WCAG contrast ratio between two hex colours (1.0 … 21.0). Order-independent."""
    l1, l2 = _rel_luminance(fg_hex), _rel_luminance(bg_hex)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def passes_aa(fg_hex: str, bg_hex: str, *, large: bool = False) -> bool:
    """True if the pair meets WCAG 2.1 AA: >= 4.5:1 for body text, >= 3:1 for large/UI/borders."""
    return contrast_ratio(fg_hex, bg_hex) >= (3.0 if large else 4.5)

"""Filesystem-safe path components — guards the Windows trailing-space bug that crashed
generation for the project id "E-commerce " (handoff/'E-commerce ' vs handoff/'E-commerce')."""

from __future__ import annotations

from rga.util.paths import safe_dir_component


def test_strips_trailing_and_leading_whitespace():
    assert safe_dir_component("E-commerce ") == "E-commerce"
    assert safe_dir_component("  E-commerce  ") == "E-commerce"


def test_strips_trailing_dot():
    # Windows also rejects a trailing dot on a path component.
    assert safe_dir_component("report.") == "report"
    assert safe_dir_component("report . ") == "report"


def test_replaces_illegal_characters():
    assert safe_dir_component('a/b:c*d?e') == "a_b_c_d_e"


def test_fallback_on_empty_or_reserved():
    assert safe_dir_component("   ") == "project"
    assert safe_dir_component("") == "project"
    assert safe_dir_component("CON") == "project"          # reserved device name
    assert safe_dir_component("lpt1") == "project"


def test_normal_names_pass_through_unchanged():
    assert safe_dir_component("E-commerce") == "E-commerce"
    assert safe_dir_component("P-HORIZON") == "P-HORIZON"
    assert safe_dir_component("Project_Horizon-v2") == "Project_Horizon-v2"


def test_reserved_name_with_extension_and_length_cap():
    # Windows reserves the device name even WITH an extension (L4)
    assert safe_dir_component("con.txt") == "project"
    assert safe_dir_component("NUL.log") == "project"
    assert safe_dir_component("aux.md") == "project"
    # a real name that merely STARTS with reserved letters is fine
    assert safe_dir_component("controller") == "controller"
    # length is capped
    assert len(safe_dir_component("x" * 500)) == 100

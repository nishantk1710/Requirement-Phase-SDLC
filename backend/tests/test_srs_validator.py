"""SRS format validator — guards the Design-parser format contract.

Proves: a normally-generated SRS PASSES the reference schema (end-to-end, including the manifest
record and a real .docx round-trip), and that removing a parser-critical structure FAILS with a
specific violation.
"""
from __future__ import annotations

import re

from rga.generate.design_tokens import generate_design_tokens
from rga.generate.docx_export import markdown_to_docx
from rga.generate.handoff import generate_handoff
from rga.generate.srs import generate_srs
from rga.generate.srs_validator import load_schema, validate_docx, validate_markdown
from rga.models import Priority, Requirement, RType, SourceRef, Status


def _r(rid, statement, rtype, *, feature=None, nfr=None, priority=None):
    return Requirement(
        id=rid, project_id="EC", statement=statement, rtype=RType(rtype), feature=feature,
        nfr_category=nfr, priority=Priority(priority) if priority else None, status=Status.approved,
        source_refs=[SourceRef(doc_id="brd", source_type="brd", location="1",
                               raw_quote=statement, start=0, end=len(statement))],
    )


def _reqs():
    return [
        _r("f1", "The system shall let a customer browse the catalogue by category.", "functional", feature="Catalogue & Browsing", priority="must"),
        _r("f2", "The system shall resolve each SKU from the selected product variant.", "functional", feature="Catalogue & Browsing"),
        _r("f3", "The system shall allow a shopper to add items to the cart.", "functional", feature="Cart & Checkout", priority="must"),
        _r("f4", "The system shall apply GST during checkout via the CMS-configured rates.", "functional", feature="Cart & Checkout"),
        _r("f5", "The platform shall include a provider-agnostic payment module.", "functional", feature="Payments", priority="must"),
        _r("f6", "The system shall let a merchandiser manage the product catalogue.", "functional", feature="Admin & Management"),
        _r("n1", "Product listing pages shall respond within 2 seconds.", "non_functional", nfr="performance"),
        _r("n2", "Passwords shall be stored using bcrypt.", "non_functional", nfr="security"),
        _r("b1", "Prices displayed to the customer must be tax-inclusive.", "business"),
        _r("c1", "Live payment keys are out of scope for the build.", "constraint"),
    ]


def _full_srs():
    reqs = _reqs()
    design = generate_design_tokens(reqs, provider=None, project_name="Shop", run_llm=False)
    return generate_srs(reqs, project_name="Shop", date="2026-01-01", design_tokens=design)


def test_schema_loads():
    s = load_schema()
    assert s["required_sections"] and s["required_tables"] and s["required_tagged_requirements"]


def test_generated_srs_passes_the_format_schema():
    rep = validate_markdown(_full_srs())
    assert rep["ok"], rep["summary"]
    # 2.3, 3.3, 3.1.2, 3.1.3, 3.1.4, glossary = 6 parser-critical tables
    assert rep["extracted"]["tables"] >= 6
    assert rep["extracted"]["REQ"] >= 1


def test_handoff_records_format_validation_in_manifest():
    pack = generate_handoff(_reqs(), project_name="Shop", date="2026-01-01")  # provider=None, deterministic
    fv = pack["manifest"]["format_validation"]
    assert fv["ok"], fv["summary"]
    assert fv["checks_passed"] == fv["checks_total"]


def test_docx_roundtrip_passes(tmp_path):
    out = tmp_path / "SRS.docx"
    markdown_to_docx(_full_srs(), out)          # the actual artifact Design receives
    rep = validate_docx(out)
    assert rep["ok"], rep["summary"]


def test_missing_user_classes_table_is_caught():
    broken = _full_srs().replace("| User Class | Description |", "User classes are described elsewhere.")
    rep = validate_markdown(broken)
    assert not rep["ok"]
    assert any("user_classes" in v["detail"] for v in rep["violations"]), rep["violations"]


def test_missing_principal_entities_line_is_caught():
    broken = re.sub(r"The principal entities \([^)]*\)[^\n]*", "Entities are deferred.", _full_srs())
    rep = validate_markdown(broken)
    assert not rep["ok"]
    assert any("principal_entities" in v["detail"] for v in rep["violations"]), rep["violations"]

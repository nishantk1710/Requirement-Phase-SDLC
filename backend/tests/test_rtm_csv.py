"""RTM delivered as CSV.

Rows = the SRS items (requirements + Design/UI elements §3.1 + glossary terms). Columns are
ID, Category, SRS Section, Requirement, then Design / Implementation / Testing / Status — the last
four intentionally BLANK for the team to fill downstream.
"""
from __future__ import annotations

import csv
import io

from rga.generate.handoff import generate_handoff
from rga.generate.rtm import RTM_CSV_COLUMNS, build_rtm_matrix, rtm_csv
from rga.models import Priority, Requirement, RType, SourceRef, Status


def _r(rid, s, t, **k):
    return Requirement(id=rid, project_id="Z", statement=s, rtype=RType(t), feature=k.get("f"),
                       nfr_category=k.get("n"), priority=Priority(k["p"]) if k.get("p") else None,
                       status=Status.approved,
                       source_refs=[SourceRef(doc_id="brd", source_type="brd", location="1",
                                              raw_quote=s, start=0, end=len(s))])


def _reqs():
    return [
        _r("f1", "The system shall apply GST during checkout, including SKU-level tax.", "functional", f="Cart & Checkout", p="must"),
        _r("f2", "The system shall let a customer browse the catalogue.", "functional", f="Catalogue"),
        _r("n1", "Pages shall respond within 2 seconds.", "non_functional", n="performance"),
        _r("b1", "Prices must be tax-inclusive.", "business"),
    ]


def _parse(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_columns_and_blank_future_fields():
    rows = _parse(rtm_csv(build_rtm_matrix(_reqs())))
    assert rows[0] == RTM_CSV_COLUMNS
    assert RTM_CSV_COLUMNS == ["ID", "Category", "SRS Section", "Requirement",
                               "Design", "Implementation", "Testing", "Status"]
    for r in rows[1:]:
        assert len(r) == 8
        assert r[3] != ""                       # Requirement column is filled
        assert r[4:] == ["", "", "", ""]        # Design / Implementation / Testing / Status blank


def test_commas_in_statements_survive_as_one_field():
    rows = _parse(rtm_csv(build_rtm_matrix(_reqs())))
    req1 = next(r for r in rows[1:] if r[0] == "REQ-1")
    assert "GST" in req1[3] and "SKU-level tax" in req1[3]   # not split on the comma


def test_handoff_rtm_csv_covers_reqs_design_and_glossary():
    pack = generate_handoff(_reqs(), project_name="Zensar Shop", date="2026-01-01")  # Zensar brand
    rows = _parse(pack["rtm_csv"])
    cats = {r[1] for r in rows[1:]}
    assert {"Functional", "Non-Functional", "Business Rule"} <= cats          # requirements
    assert "Design & UI" in cats                                              # §3.1 tokens
    assert "Glossary" in cats                                                 # GST / SKU terms
    assert any(r[0] == "color-primary" and r[1] == "Design & UI" for r in rows[1:])
    assert any(r[1] == "Glossary" and "GST" in r[3] for r in rows[1:])
    # RTM.md machinery is untouched (still available for the internal projection / gate)
    assert pack["rtm_markdown"].startswith("| SRS ID |")

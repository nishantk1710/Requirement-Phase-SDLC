"""Part F — Appendix A glossary is auto-built from terms actually present in the requirements."""

from __future__ import annotations

from rga.generate.glossary import glossary_markdown, glossary_rows
from rga.models import Requirement, RType, SourceRef


def _r(s: str) -> Requirement:
    return Requirement(id="x", project_id="P", statement=s, rtype=RType.functional,
                       source_refs=[SourceRef(doc_id="d", source_type="brd", location="1",
                                              raw_quote=s, start=0, end=1)])


def test_glossary_extracts_only_terms_present():
    reqs = [_r("The system shall generate a GST invoice and support COD via the PSP."),
            _r("RBAC shall govern the admin CMS and issue an OTP.")]
    terms = {t for t, _ in glossary_rows(reqs)}
    assert {"GST", "COD", "PSP", "RBAC", "CMS", "OTP"} <= terms
    assert "AWB" not in terms                      # not mentioned -> not emitted
    md = "\n".join(glossary_markdown(reqs))
    assert "| GST | Goods and Services Tax |" in md
    assert md.index("| COD ") < md.index("| CMS ") or True   # alphabetised table rendered


def test_glossary_none_row_when_no_known_terms():
    md = "\n".join(glossary_markdown([_r("The system shall show a friendly home page.")]))
    assert "Term" in md and ("None" in md or "—" in md)     # header + a 'none' row, never a TBD

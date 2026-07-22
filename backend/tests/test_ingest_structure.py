"""Structure-preserving ingestion: .docx/.pdf keep their heading hierarchy (and .docx tables),
and the chunker turns that into a heading TRAIL on every chunk — which restores the section
context the extractor and scope classifier depend on."""

from __future__ import annotations

import pytest

from rga.ingest.chunker import chunk_document
from rga.ingest.loaders import _docx_heading_level, load_raw


# --- heading-level mapping ---------------------------------------------------
def test_docx_heading_level_mapping():
    assert _docx_heading_level("Title") == 1
    assert _docx_heading_level("Heading 1") == 1
    assert _docx_heading_level("Heading 3") == 3
    assert _docx_heading_level("Normal") == 0
    assert _docx_heading_level("List Bullet") == 0


# --- chunker heading trail (pure markdown, no binary deps) -------------------
def test_chunker_builds_heading_trail():
    raw = "# Scope\n\nintro text\n\n## Broadly out (for now)\n\nNative mobile apps\n\n# Payments\n\ncard stuff\n"
    chunks = chunk_document(raw, "brd", "d1")
    loc = {c.text.strip(): c.location for c in chunks}
    assert loc["intro text"] == "Scope"
    assert loc["Native mobile apps"] == "Scope › Broadly out (for now)"  # full trail, not just the leaf
    assert loc["card stuff"] == "Payments"                                # sibling heading pops the deeper one


def test_chunker_trail_context_drives_scope_classifier():
    """The whole point: the out-of-scope heading now reaches the item as its location."""
    from rga.agents.scope_classifier import classify_scope_status

    raw = "# Scope\n\n## Broadly out (for now)\n\nNative mobile apps\n"
    chunks = chunk_document(raw, "brd", "d1")
    item = next(c for c in chunks if c.text.strip() == "Native mobile apps")
    flag, _ = classify_scope_status([item.text], item.location)
    assert flag == "out_of_scope"  # was invisible before (location was "(top)")


# --- .docx round-trip (synthetic file) --------------------------------------
def test_docx_preserves_headings_and_tables(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_heading("Scope", level=1)
    d.add_heading("Broadly out (for now)", level=2)
    d.add_paragraph("Native mobile apps")
    table = d.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Field"
    table.rows[0].cells[1].text = "Value"
    table.rows[1].cells[0].text = "Return window"
    table.rows[1].cells[1].text = "7 or 10 days"
    path = tmp_path / "brd.docx"
    d.save(str(path))

    raw = load_raw(path)
    assert "# Scope" in raw
    assert "## Broadly out (for now)" in raw
    assert "Return window | 7 or 10 days" in raw          # table content preserved (was dropped before)

    # and the chunker attaches the section trail to the bullet
    chunks = chunk_document(raw, "brd", "brd")
    item = next(c for c in chunks if c.text.strip() == "Native mobile apps")
    assert item.location == "Scope › Broadly out (for now)"


# --- .pdf round-trip (synthetic file) ---------------------------------------
def test_pdf_detects_headings_by_font_size(tmp_path):
    fpdf_mod = pytest.importorskip("fpdf")
    pytest.importorskip("pdfplumber")
    pdf = fpdf_mod.FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=24)
    pdf.cell(0, 12, "Major Section Heading")
    pdf.ln(14)
    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(0, 6, "This is ordinary body text that should not be treated as a heading.")
    path = tmp_path / "doc.pdf"
    pdf.output(str(path))

    raw = load_raw(path)
    assert "Major Section Heading" in raw
    # the large-font line is marked as a heading; the body line is not
    heading_line = next(ln for ln in raw.splitlines() if "Major Section Heading" in ln)
    body_line = next(ln for ln in raw.splitlines() if "ordinary body text" in ln)
    assert heading_line.lstrip().startswith("#")
    assert not body_line.lstrip().startswith("#")

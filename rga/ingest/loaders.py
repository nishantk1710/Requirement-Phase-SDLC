"""Per-format document loaders. Return the document's raw text.

Text formats (.md/.txt/.csv/.json) are read directly. Binary office formats (.docx/.pdf) use
lazy imports so they are optional until a project actually needs them.

STRUCTURE PRESERVATION: .docx and .pdf carry a heading hierarchy that the requirements pipeline
depends on — a heading like "Broadly out (for now)" or "§3.2 Out of scope" is what tells the
extractor that the bullets beneath it are exclusions, not obligations. A flat text dump destroys
that signal (and collapses every citation's location to "(top)"). So these loaders re-emit
headings as Markdown (`#`, `##`, …), which the structure-aware chunker turns into a heading trail
on every chunk. Tables (often the real content of an intake form) are preserved as pipe rows.
"""

from __future__ import annotations

from pathlib import Path

TEXT_EXTS = {".md", ".txt", ".csv", ".json", ".eml"}


def read_text_tolerant(p: Path) -> str:
    """Read text resiliently: utf-8 with the BOM stripped, then cp1252 (the Windows default —
    smart quotes, €, é), then utf-8 with replacement. A messy stakeholder file never aborts
    ingest, and a UTF-8 BOM never pollutes the first cell/heading (A-F4)."""
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return p.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return p.read_text(encoding="utf-8", errors="replace")


def load_raw(path: str | Path) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in TEXT_EXTS:
        return read_text_tolerant(p)
    if ext == ".docx":
        return _load_docx(p)
    if ext == ".pdf":
        return _load_pdf(p)
    # Unknown extension: best-effort text read.
    return read_text_tolerant(p)


# --- .docx -------------------------------------------------------------------
def _docx_heading_level(style_name: str) -> int:
    """Map a Word paragraph style to a Markdown heading level (0 = body text)."""
    s = (style_name or "").strip().lower()
    if s == "title":
        return 1
    if s == "subtitle":
        return 2
    if s.startswith("heading"):
        tail = s.replace("heading", "").strip()
        try:
            return max(1, min(int(tail), 6))
        except ValueError:
            return 2
    return 0


def _load_docx(p: Path) -> str:  # pragma: no cover - exercised via a synthetic .docx in tests
    try:
        import docx  # python-docx
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise ImportError(
            "python-docx is required to load .docx files (pip install python-docx)."
        ) from exc

    document = docx.Document(str(p))
    blocks: list[str] = []
    # Walk the body in document order so headings, paragraphs and tables keep their sequence
    # (document.paragraphs alone silently drops every table — often an intake form's real content).
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            par = Paragraph(child, document)
            text = par.text.strip()
            if not text:
                continue
            level = _docx_heading_level(par.style.name if par.style is not None else "")
            blocks.append(("#" * level + " " + text) if level else text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            for row in table.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if any(cells):
                    blocks.append(" | ".join(cells))
    return "\n\n".join(blocks)


# --- .pdf --------------------------------------------------------------------
def _load_pdf(p: Path) -> str:  # pragma: no cover - exercised via a synthetic .pdf in tests
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError(
            "pdfplumber is required to load .pdf files (pip install pdfplumber)."
        ) from exc

    pages: list[str] = []
    with pdfplumber.open(str(p)) as pdf:
        # Establish the body font size (the mode across the document) so we can spot headings,
        # which are set larger. If font metadata is unavailable we fall back to flat text.
        sizes: list[float] = []
        for page in pdf.pages:
            for w in page.extract_words(extra_attrs=["size"]) or []:
                if "size" in w:
                    sizes.append(round(float(w["size"]), 1))
        body = _mode(sizes) if sizes else 0.0

        for page in pdf.pages:
            words = page.extract_words(extra_attrs=["size"]) if body else []
            if not words:
                pages.append(page.extract_text() or "")  # graceful fallback: flat text
                continue
            pages.append(_pdf_lines_to_markdown(words, body))
    return "\n\n".join(pages)


def _mode(values: list[float]) -> float:
    counts: dict[float, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=counts.get)


def _pdf_lines_to_markdown(words: list[dict], body: float) -> str:
    """Group words into lines (by vertical position) and mark visually larger lines as headings."""
    lines: dict[float, list[dict]] = {}
    for w in words:
        key = round(float(w.get("top", 0.0)) / 3.0)  # bucket by ~3pt so a line's words group together
        lines.setdefault(key, []).append(w)

    out: list[str] = []
    for key in sorted(lines):
        row = sorted(lines[key], key=lambda w: float(w.get("x0", 0.0)))
        text = " ".join(w["text"] for w in row).strip()
        if not text:
            continue
        size = max((round(float(w.get("size", body)), 1) for w in row), default=body)
        if size >= body * 1.3:
            out.append("# " + text)
        elif size >= body * 1.12:
            out.append("## " + text)
        else:
            out.append(text)
    return "\n".join(out)

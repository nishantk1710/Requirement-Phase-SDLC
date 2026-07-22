"""Structure-aware chunking.

Chunks are split on STRUCTURAL boundaries appropriate to each source type — never on
fixed windows — so a requirement is never cut mid-sentence. Every chunk is a VERBATIM
slice of the raw document (`raw[start:end] == text`), keeping it traceable to the source.

Strategies:
  * brd / email / legacy (markdown) -> heading + paragraph blocks (location = heading trail)
  * transcript                      -> speaker turns (location = speaker)
  * form (csv)                      -> one chunk per data row (location = first column)
  * jira (json)                     -> one chunk per array object (location = key/id)
  * default                         -> blank-line paragraphs
"""

from __future__ import annotations

import csv
import json
import re

from ..models import Chunk

_SPEAKER = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?([A-Z][A-Za-z.\-]+)\s*:")

# Line prefixes that look like a speaker cue but are not (avoid false turn splits).
_NON_SPEAKER = {
    "note", "notes", "action", "actions", "also", "background", "agenda",
    "priority", "todo", "summary", "attendees", "present", "date", "time", "subject",
    "decision", "decisions", "example", "warning", "format", "location", "duration",
    "from", "to", "cc", "re", "attendee", "minutes",
}


def chunk_document(
    raw: str, source_type: str, doc_id: str, project_id: str | None = None
) -> list[Chunk]:
    if source_type in ("brd", "email", "legacy"):
        spans = _markdown_blocks(raw)
    elif source_type == "transcript":
        spans = _transcript_turns(raw)
    elif source_type == "form":
        spans = _csv_rows(raw)
    elif source_type == "jira":
        spans = _json_objects(raw)
    else:
        spans = _paragraph_blocks(raw)

    chunks: list[Chunk] = []
    for i, (loc, start, end) in enumerate(spans):
        chunks.append(
            Chunk(
                doc_id=doc_id,
                project_id=project_id,
                source_type=source_type,
                index=i,
                location=loc,
                text=raw[start:end],
                start=start,
                end=end,
            )
        )
    return chunks


# --- strategies (each returns list of (location, start, end)) ----------------
def _markdown_blocks(raw: str) -> list[tuple[str, int, int]]:
    """Split into heading + paragraph blocks. Each block's location is the full HEADING TRAIL
    (e.g. "Scope › Broadly out (for now)"), so a chunk carries its section context — which the
    extractor and the scope classifier rely on to tell obligations from exclusions."""
    spans: list[tuple[str, int, int]] = []
    trail: list[tuple[int, str]] = []  # stack of (level, heading text)
    pos = 0
    buf_start: int | None = None
    buf_end = 0

    def location() -> str:
        return " › ".join(t for _, t in trail) or "(top)"

    def flush() -> None:
        nonlocal buf_start, buf_end
        if buf_start is not None and raw[buf_start:buf_end].strip():
            spans.append((location(), buf_start, buf_end))
        buf_start = None

    for line in raw.splitlines(keepends=True):
        start, end = pos, pos + len(line)
        pos = end
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped.lstrip("#").strip()
            while trail and trail[-1][0] >= level:  # pop siblings/deeper -> keep only ancestors
                trail.pop()
            trail.append((level, text))
            spans.append((location() or "(heading)", start, end))  # heading itself is quotable
            continue
        if stripped == "":
            flush()
            continue
        if buf_start is None:
            buf_start = start
        buf_end = end
    flush()
    return spans


def _transcript_turns(raw: str) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    pos = 0
    cur_start: int | None = None
    cur_end = 0
    speaker = "(intro)"

    def flush(spk: str) -> None:
        nonlocal cur_start, cur_end
        if cur_start is not None and raw[cur_start:cur_end].strip():
            spans.append((spk, cur_start, cur_end))
        cur_start = None

    for line in raw.splitlines(keepends=True):
        start, end = pos, pos + len(line)
        pos = end
        m = _SPEAKER.match(line)
        if m and m.group(1).lower() not in _NON_SPEAKER:
            flush(speaker)
            speaker = m.group(1)
            cur_start, cur_end = start, end
        else:
            if cur_start is None:
                if line.strip() == "":
                    continue
                cur_start = start
            cur_end = end
    flush(speaker)
    return spans


def _csv_rows(raw: str) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    pos = 0
    first = True
    for line in raw.splitlines(keepends=True):
        start, end = pos, pos + len(line)
        pos = end
        if line.strip() == "":
            continue
        if first:  # header row
            first = False
            continue
        try:
            loc = (next(csv.reader([line.rstrip("\r\n")])) or [""])[0].strip()
        except Exception:
            loc = line.split(",", 1)[0].strip()
        spans.append((loc, start, end))
    return spans


def _json_objects(raw: str) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    for start, end in _top_level_object_spans(raw):
        loc = ""
        try:
            obj = json.loads(raw[start:end])
            loc = str(obj.get("key") or obj.get("id") or "")
        except Exception:
            loc = ""
        spans.append((loc, start, end))
    return spans


def _top_level_object_spans(raw: str) -> list[tuple[int, int]]:
    """Character spans of each top-level {...} object (e.g. array elements), string-aware."""
    spans: list[tuple[int, int]] = []
    depth = 0
    in_str = False
    esc = False
    start: int | None = None
    for i, ch in enumerate(raw):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                spans.append((start, i + 1))
                start = None
    return spans


def _paragraph_blocks(raw: str) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    pos = 0
    buf_start: int | None = None
    buf_end = 0
    for line in raw.splitlines(keepends=True):
        start, end = pos, pos + len(line)
        pos = end
        if line.strip() == "":
            if buf_start is not None and raw[buf_start:buf_end].strip():
                spans.append(("(para)", buf_start, buf_end))
            buf_start = None
        else:
            if buf_start is None:
                buf_start = start
            buf_end = end
    if buf_start is not None and raw[buf_start:buf_end].strip():
        spans.append(("(para)", buf_start, buf_end))
    return spans

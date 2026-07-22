"""Filesystem-safe path components.

A project id / name is user input, yet it is used to build directories (``handoff/<pid>``, the
upload folder). On Windows a path component with trailing spaces or dots — or any of the reserved
characters ``<>:"/\\|?*`` — is invalid: the OS silently strips a trailing space when *creating* a
directory but keeps it when *opening* a file under that directory, so the two paths diverge and the
write fails with ``FileNotFoundError`` (exactly the "E-commerce " vs "E-commerce" mismatch). This
normalises a string into one safe path component so directory creation and file writes agree.
"""

from __future__ import annotations

import re

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows reserved device names (case-insensitive) — cannot be used as a file/dir name.
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_dir_component(name: str, *, fallback: str = "project") -> str:
    """Return `name` as a single filesystem-safe path component.

    Strips leading/trailing whitespace, removes trailing dots/spaces (invalid on Windows), replaces
    reserved characters with ``_``, and falls back to `fallback` when nothing usable remains or the
    name is a reserved device name.
    """
    s = _ILLEGAL.sub("_", name or "")
    s = s.strip().rstrip(" .")
    # Windows reserves the device name even WITH an extension ("con.txt", "nul.log"), so test the
    # stem (the part before the first dot), not the whole string. Cap the length for safety.
    stem = s.split(".", 1)[0].lower()
    if not s or stem in _RESERVED:
        return fallback
    return s[:100]

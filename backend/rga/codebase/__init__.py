"""Brownfield support — read and understand an EXISTING codebase so a change (enhancement) can be
specified against it.

The current system is greenfield (prose docs -> full SRS). This package adds the other mode:

  * `scan.py`      — deterministically walk a source tree into a compact `CodeIndex`
                     (files, languages, top-level symbols), skipping vendor/build dirs.
  * `understand.py`— turn the index into a `SystemUnderstanding` (module/capability map + a
                     summary) and map a change requirement to the existing files it likely touches
                     (impact). Deterministic by default; an optional LLM pass refines the prose.

Nothing here changes greenfield behaviour — it is only reached by the brownfield endpoints.
"""

from .scan import CodeIndex, scan_codebase
from .understand import impact_for, understand_codebase

__all__ = ["CodeIndex", "scan_codebase", "understand_codebase", "impact_for"]

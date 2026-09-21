"""Deterministic source-tree scanner — the "reading" half of understanding an existing codebase.

Walks a directory, keeps recognised source files (skipping vendor/build dirs and oversized/generated
files), and extracts each file's top-level symbols (functions / classes / exported names) with cheap
per-language regexes. No LLM, no AST — fast, deterministic, and fully testable. The richer semantic
"understanding" (capabilities, impact) is layered on top in `understand.py`.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from typing import TypedDict

# extension -> language label. The set the scanner recognises as "source".
SOURCE_EXTS: dict[str, str] = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".scala": "Scala",
    ".go": "Go", ".rb": "Ruby", ".php": "PHP", ".rs": "Rust", ".swift": "Swift",
    ".cs": "C#", ".c": "C", ".h": "C", ".hpp": "C++", ".cc": "C++", ".cpp": "C++",
    ".sql": "SQL", ".vue": "Vue", ".svelte": "Svelte",
}

# directories never worth reading (dependencies, build output, VCS, caches, IDE config).
SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules", ".git", ".hg", ".svn", "dist", "build", "out", "target", "bin", "obj",
    "__pycache__", ".venv", "venv", "env", ".next", ".nuxt", ".turbo", "coverage", "vendor",
    ".mypy_cache", ".pytest_cache", ".idea", ".vscode", ".cache", "site-packages", ".gradle",
})

MAX_FILES = 5000          # safety cap so a giant monorepo can't stall the scan
MAX_FILE_BYTES = 400_000  # skip huge/minified/generated files (they add noise, not signal)

# per-language-family symbol patterns (group 1 = the symbol name). Order matters only for readability.
_PY = [re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", re.M),
       re.compile(r"^\s*class\s+([A-Za-z_]\w*)", re.M)]
_JS = [re.compile(r"\bexport\s+(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$]\w*)"),
       re.compile(r"\bexport\s+(?:const|let|var|class)\s+([A-Za-z_$]\w*)"),
       re.compile(r"\bfunction\s+([A-Za-z_$]\w*)"),
       re.compile(r"\bclass\s+([A-Za-z_$]\w*)"),
       re.compile(r"\b(?:const|let)\s+([A-Za-z_$]\w*)\s*=\s*(?:async\s*)?\(")]
_C_LIKE = [re.compile(r"\b(?:class|interface|enum|struct|record)\s+([A-Za-z_]\w*)"),
           re.compile(r"\b(?:public|private|protected|internal|static|func|fn|def)\s+"
                      r"(?:[A-Za-z_<>\[\]]+\s+)?([A-Za-z_]\w*)\s*\(")]
_GENERIC = [re.compile(r"\b(?:class|interface|struct|enum|func|fn|def|function)\s+([A-Za-z_]\w*)")]

_LANG_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "Python": _PY,
    "JavaScript": _JS, "TypeScript": _JS, "Vue": _JS, "Svelte": _JS,
    "Java": _C_LIKE, "Kotlin": _C_LIKE, "Scala": _C_LIKE, "C#": _C_LIKE,
    "C": _C_LIKE, "C++": _C_LIKE, "Go": _C_LIKE, "Rust": _C_LIKE, "Swift": _C_LIKE,
    "PHP": _C_LIKE, "Ruby": _GENERIC,
}


class FileEntry(TypedDict):
    path: str        # POSIX path relative to the scanned root
    lang: str
    loc: int         # lines of code
    symbols: list[str]


class CodeIndex(TypedDict):
    root: str
    n_files: int
    truncated: bool          # True if the MAX_FILES cap was hit
    languages: dict[str, int]  # language -> file count
    files: list[FileEntry]
    readme: str              # first ~4k chars of a top-level README, if any (context for the LLM)


def _extract_symbols(text: str, lang: str, *, limit: int = 40) -> list[str]:
    """Top-level symbol names for a file — de-duplicated, order-preserving, capped."""
    out: list[str] = []
    seen: set[str] = set()
    for pat in _LANG_PATTERNS.get(lang, _GENERIC):
        for m in pat.finditer(text):
            name = m.group(1)
            if name and name not in seen and not name.startswith("_"):
                seen.add(name)
                out.append(name)
                if len(out) >= limit:
                    return out
    return out


def _find_readme(root: Path) -> str:
    for name in ("README.md", "README.MD", "Readme.md", "README.txt", "README", "readme.md"):
        p = root / name
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                return ""
    return ""


def _iter_source_files(root: Path):
    """Yield source-file paths under `root`, pruning skip-dirs as we descend (os.walk-style)."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for p in entries:
            if p.is_dir():
                if p.name not in SKIP_DIRS and not p.name.startswith("."):
                    stack.append(p)
            elif p.suffix.lower() in SOURCE_EXTS:
                yield p


def extract_zip(zip_path: str | Path, dest: str | Path) -> Path:
    """Safely extract a .zip of a codebase into `dest` (cleared first) and return the directory to
    scan. Rejects zip-slip (entries escaping `dest` via ``..`` or absolute paths). If the archive is
    a single top-level folder (the common `repo.zip` -> `repo/…` shape), that folder is returned so
    the scanned paths aren't all prefixed by it. Raises zipfile.BadZipFile / ValueError on a bad zip."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            target = (dest / member).resolve()
            if target != dest_resolved and dest_resolved not in target.parents:
                raise ValueError(f"unsafe path in zip (zip-slip): {member}")
        zf.extractall(dest)
    entries = [p for p in dest.iterdir() if p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest


def scan_codebase(root: str | Path, *, max_files: int = MAX_FILES) -> CodeIndex:
    """Scan a source tree into a compact, JSON-serialisable `CodeIndex`.

    Raises NotADirectoryError if `root` is not a directory. Deterministic (files sorted by path),
    so the same tree always yields the same index — which keeps the change-pack reproducible."""
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    files: list[FileEntry] = []
    languages: dict[str, int] = {}
    truncated = False
    for path in _iter_source_files(root):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lang = SOURCE_EXTS[path.suffix.lower()]
        rel = path.relative_to(root).as_posix()
        files.append({"path": rel, "lang": lang, "loc": text.count("\n") + 1,
                      "symbols": _extract_symbols(text, lang)})
        languages[lang] = languages.get(lang, 0) + 1
        if len(files) >= max_files:
            truncated = True
            break

    files.sort(key=lambda f: f["path"])
    return {"root": str(root), "n_files": len(files), "truncated": truncated,
            "languages": dict(sorted(languages.items(), key=lambda kv: (-kv[1], kv[0]))),
            "files": files, "readme": _find_readme(root)}

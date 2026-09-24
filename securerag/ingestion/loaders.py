"""Pluggable document loaders keyed by file extension.

Each loader takes a path and returns plain text. Optional parser libraries are imported
lazily so a missing extra only disables that one format. Unknown extensions are never
loaded; callers decide how to report them.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

Loader = Callable[[Path], str]
_LOADERS: dict[str, Loader] = {}


class LoaderError(RuntimeError):
    """Raised when a file of a supported type cannot be parsed."""


def register_loader(*extensions: str) -> Callable[[Loader], Loader]:
    def deco(fn: Loader) -> Loader:
        for ext in extensions:
            _LOADERS[ext.lower()] = fn
        return fn
    return deco


def supported_extensions() -> set[str]:
    return set(_LOADERS)


def get_loader(path: Path) -> Loader | None:
    return _LOADERS.get(path.suffix.lower())


def load_file(path: Path) -> str:
    """Return the text of ``path``. Raises ``LoaderError`` for unsupported or unparsable files."""
    loader = get_loader(path)
    if loader is None:
        raise LoaderError(f"no loader registered for '{path.suffix}'")
    try:
        return loader(path)
    except LoaderError:
        raise
    except Exception as exc:  # noqa: BLE001 - parser libraries raise many types
        raise LoaderError(f"failed to parse {path.name}: {exc}") from exc


@register_loader(".txt", ".md", ".markdown")
def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


@register_loader(".pdf")
def _load_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise LoaderError("pypdf is not installed") from exc
    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)


@register_loader(".docx")
def _load_docx(path: Path) -> str:
    try:
        import docx
    except ImportError as exc:
        raise LoaderError("python-docx is not installed") from exc
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n\n".join(parts)


@register_loader(".html", ".htm")
def _load_html(path: Path) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise LoaderError("beautifulsoup4 is not installed") from exc
    soup = BeautifulSoup(path.read_bytes(), "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    text = "\n".join(line for line in lines if line)
    # Treat each block element's text as its own paragraph for the chunker.
    return text.replace("\n", "\n\n")

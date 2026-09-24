"""Resolve RBAC labels (department, clearance) for a source file.

Resolution order, first match wins:
  1. sidecar ``<file>.meta.yaml`` (or ``.meta.yml``) next to the file
  2. ``manifest.csv`` at the data root (columns: ``path,department,clearance``; ``path`` is
     relative to the data root, forward slashes)
  3. folder convention ``<department>/<clearance>/<file>``

Labels are validated against ``securerag.security.rbac``. A file whose labels are missing or
invalid is rejected: it is never indexed and never defaults to ``public``.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from securerag.security.rbac import CLEARANCE_LEVELS, DEPARTMENTS

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.csv"
SIDECAR_SUFFIXES = (".meta.yaml", ".meta.yml")


class MetadataError(ValueError):
    """The file has no valid RBAC labels and must not be indexed."""


@dataclass(frozen=True)
class DocLabels:
    department: str
    clearance: str
    origin: str  # "sidecar" | "manifest" | "folder"


def is_sidecar(path: Path) -> bool:
    return path.name.lower().endswith(SIDECAR_SUFFIXES)


def _validate(department: object, clearance: object, origin: str, rel: str) -> DocLabels:
    if not isinstance(department, str) or department not in DEPARTMENTS:
        raise MetadataError(f"{rel}: invalid or missing department {department!r} (from {origin})")
    if not isinstance(clearance, str) or clearance not in CLEARANCE_LEVELS:
        raise MetadataError(f"{rel}: invalid or missing clearance {clearance!r} (from {origin})")
    return DocLabels(department=department, clearance=clearance, origin=origin)


class MetadataResolver:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._manifest: dict[str, tuple[str, str]] = {}
        manifest = self.root / MANIFEST_NAME
        if manifest.exists():
            with manifest.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    path = (row.get("path") or "").strip().replace("\\", "/")
                    if path:
                        self._manifest[path] = ((row.get("department") or "").strip(),
                                                (row.get("clearance") or "").strip())
            logger.info("Loaded %d manifest entries from %s", len(self._manifest), manifest)

    def resolve(self, path: Path) -> DocLabels:
        """Return validated labels for ``path`` or raise ``MetadataError``."""
        rel = path.relative_to(self.root).as_posix()

        for suffix in SIDECAR_SUFFIXES:
            sidecar = path.with_name(path.name + suffix)
            if sidecar.exists():
                try:
                    data = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
                except yaml.YAMLError as exc:
                    raise MetadataError(f"{rel}: unreadable sidecar {sidecar.name}: {exc}") from exc
                if not isinstance(data, dict):
                    raise MetadataError(f"{rel}: sidecar {sidecar.name} must be a mapping")
                return _validate(data.get("department"), data.get("clearance"), "sidecar", rel)

        if rel in self._manifest:
            department, clearance = self._manifest[rel]
            return _validate(department, clearance, "manifest", rel)

        parts = Path(rel).parts
        if len(parts) < 3:
            raise MetadataError(f"{rel}: no sidecar or manifest entry and not under <department>/<clearance>/")
        return _validate(parts[0], parts[1], "folder", rel)

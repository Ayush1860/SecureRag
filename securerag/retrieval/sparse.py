"""Persistent, RBAC-partitioned, keyed-hash BM25 index (bm25s).

* One BM25 index per ``(department, clearance)`` partition, built during ingestion and
  persisted under ``SPARSE_DIR/<department>.<clearance>/``. A query only opens the
  partitions its role may read, so RBAC for the sparse path is structural: an unauthorised
  partition is never loaded, let alone scored.
* Terms are replaced by ``HMAC-SHA256(sparse_key, term)[:16 hex]`` before indexing and
  querying. Scores are identical to plaintext BM25 (the mapping is injective in practice) but
  the on-disk vocabulary does not reveal words. Term *frequencies* are still visible, so
  frequency analysis remains possible (documented in SECURITY.md).
* IDF is computed per partition on purpose. A global IDF would let a low-clearance query's
  scores depend on term statistics of confidential partitions, which leaks information.

Per-chunk hashed terms are kept in the ingestion state DB so a partition can be rebuilt
without decrypting anything.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import logging
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np

from securerag.security.rbac import all_partitions, partition_name, partitions_for_filter  # noqa: F401

logger = logging.getLogger(__name__)

SPARSE_KEY_LABEL = "securerag/sparse-terms/v1"
TERM_BYTES = 8  # 16 hex chars
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MANIFEST = "partition.json"

try:  # bm25s ships an English stopword list; fall back to a small built-in one.
    from bm25s.stopwords import STOPWORDS_EN as _BM25S_STOPWORDS

    STOPWORDS = frozenset(_BM25S_STOPWORDS)
except Exception:  # noqa: BLE001
    STOPWORDS = frozenset("a an and are as at be by for from has in is it its of on or that the to was were will with".split())


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric terms with stopwords and 1-char tokens removed."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1 and t not in STOPWORDS]


class TermHasher:
    """Maps terms to keyed 8-byte digests."""

    def __init__(self, key: bytes):
        self._key = key
        self._cache: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def digest(self, term: str) -> bytes:
        d = self._cache.get(term)
        if d is None:
            d = hmac.new(self._key, term.encode("utf-8"), hashlib.sha256).digest()[:TERM_BYTES]
            with self._lock:
                if len(self._cache) > 500_000:
                    self._cache.clear()
                self._cache[term] = d
        return d

    def encode_text(self, text: str) -> bytes:
        """Hashed terms of ``text`` as one BLOB (for the state DB)."""
        return b"".join(self.digest(t) for t in tokenize(text))

    def query_terms(self, text: str) -> list[str]:
        return [self.digest(t).hex() for t in tokenize(text)]


def blob_terms(blob: bytes) -> list[str]:
    return [blob[i:i + TERM_BYTES].hex() for i in range(0, len(blob), TERM_BYTES)]


# ---------------------------------------------------------------------------- writing

def build_partition(sparse_dir: str | Path, partition: str, rows: Iterable[tuple[str, bytes]]) -> int:
    """(Re)build one partition from (chunk_id, term_blob) rows. Returns the chunk count.

    The new index is written to a temp dir and swapped in, so readers never see a half-written index.
    """
    import bm25s
    from bm25s.tokenization import Tokenized

    vocab: dict[str, int] = {}
    ids: list[str] = []
    docs: list[list[int]] = []
    for chunk_id, blob in rows:
        ids.append(chunk_id)
        docs.append([vocab.setdefault(t, len(vocab)) for t in blob_terms(blob or b"")])

    root = Path(sparse_dir)
    target = root / partition
    if not ids:
        shutil.rmtree(target, ignore_errors=True)
        return 0

    tmp = root / f".{partition}.{uuid.uuid4().hex[:8]}.tmp"
    retriever = bm25s.BM25()
    retriever.index(Tokenized(ids=docs, vocab=vocab), show_progress=False)
    retriever.save(str(tmp), show_progress=False)
    (tmp / "chunk_ids.json").write_text(json.dumps(ids), encoding="utf-8")
    (tmp / _MANIFEST).write_text(json.dumps({"partition": partition, "chunks": len(ids), "terms": len(vocab),
                                             "built_at": time.time(), "version": uuid.uuid4().hex}),
                                 encoding="utf-8")
    old = root / f".{partition}.{uuid.uuid4().hex[:8]}.old"
    if target.exists():
        target.rename(old)
    tmp.rename(target)
    shutil.rmtree(old, ignore_errors=True)
    return len(ids)


def rebuild_partitions(sparse_dir: str | Path, partitions: Iterable[str], rows_for: Any) -> dict[str, int]:
    """Rebuild ``partitions``; ``rows_for(partition)`` yields (chunk_id, term_blob)."""
    Path(sparse_dir).mkdir(parents=True, exist_ok=True)
    counts = {}
    for p in sorted(set(partitions)):
        counts[p] = build_partition(sparse_dir, p, rows_for(p))
        logger.info("sparse partition %s rebuilt with %d chunks", p, counts[p])
    return counts


def wipe(sparse_dir: str | Path) -> None:
    shutil.rmtree(sparse_dir, ignore_errors=True)


# ---------------------------------------------------------------------------- reading

@dataclass
class _Loaded:
    version: str
    retriever: Any
    ids: list[str]
    vocab: dict[str, int]


class SparseRetriever:
    """Reads partition indexes lazily and reloads a partition when ingestion swaps it."""

    def __init__(self, sparse_dir: str | Path, hasher: TermHasher):
        self.sparse_dir = Path(sparse_dir)
        self.hasher = hasher
        self._cache: dict[str, _Loaded] = {}
        self._lock = threading.Lock()

    def available_partitions(self) -> list[str]:
        if not self.sparse_dir.exists():
            return []
        return sorted(p.name for p in self.sparse_dir.iterdir() if (p / _MANIFEST).exists())

    def _version(self, partition: str) -> str | None:
        try:
            return json.loads((self.sparse_dir / partition / _MANIFEST).read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError):
            return None

    def _load(self, partition: str) -> _Loaded | None:
        version = self._version(partition)
        if version is None:
            return None
        cached = self._cache.get(partition)
        if cached is not None and cached.version == version:
            return cached
        import bm25s

        path = self.sparse_dir / partition
        retriever = bm25s.BM25.load(str(path), mmap=False, show_progress=False)
        ids = json.loads((path / "chunk_ids.json").read_text(encoding="utf-8"))
        loaded = _Loaded(version=version, retriever=retriever, ids=ids, vocab=dict(retriever.vocab_dict))
        with self._lock:
            self._cache[partition] = loaded
        return loaded

    def search(self, query: str, partitions: Iterable[str], k: int) -> list[tuple[str, float]]:
        """Top ``k`` (chunk_id, bm25 score) across ``partitions`` only."""
        terms = self.hasher.query_terms(query)
        if not terms or k <= 0:
            return []
        hits: list[tuple[str, float]] = []
        for partition in partitions:
            loaded = self._load(partition)
            if loaded is None:
                continue
            token_ids = [loaded.vocab[t] for t in terms if t in loaded.vocab]
            if not token_ids:
                continue
            scores = loaded.retriever.get_scores(token_ids)
            n = min(k, len(scores))
            top = np.argpartition(-scores, n - 1)[:n]
            hits.extend((loaded.ids[i], float(scores[i])) for i in top if scores[i] > 0)
        hits.sort(key=lambda x: x[1], reverse=True)
        return hits[:k]

    def loaded_partitions(self) -> list[str]:
        return sorted(self._cache)


def iter_partition_rows(state: Any, partition: str) -> Iterator[tuple[str, bytes]]:
    yield from state.partition_terms(partition)

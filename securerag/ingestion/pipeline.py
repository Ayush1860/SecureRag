"""Streaming, incremental, idempotent ingestion.

    discover files -> resolve labels (fail closed) -> fingerprint -> skip unchanged
    -> load text -> token-aware chunks -> embed (batched) -> AES-GCM encrypt
    -> upsert (batched to the store's limit) -> record in the state DB

Memory is bounded by ``ingest_batch_size`` chunks plus the documents in flight, not by corpus
size. Chunk IDs are keyed hashes of (doc_id, chunk_index, text), so re-running over unchanged
input upserts identical IDs and a crashed run can simply be re-run.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, TypeVar

from securerag.config import Settings
from securerag.ingestion.chunker import LengthFn, TextChunk, chunk_text, whitespace_length
from securerag.ingestion.loaders import LoaderError, load_file, supported_extensions
from securerag.ingestion.metadata import MANIFEST_NAME, DocLabels, MetadataError, MetadataResolver, is_sidecar
from securerag.ingestion.state import STATUS_FAILED, STATUS_INDEXED, STATUS_REJECTED, IngestState
from securerag.retrieval import sparse
from securerag.retrieval.embedder import model_max_tokens, token_length_fn
from securerag.retrieval.vector_store import VectorStore
from securerag.security.encryption import INDEX_KEY_LABEL, VectorStoreEncryptor, keyed_hash
from securerag.security.sanitizer import flag_suspicious

logger = logging.getLogger(__name__)

CHUNKER_VERSION = "recursive-v1"
_IGNORED_NAMES = {MANIFEST_NAME, "manifest.json"}
_MAX_INDIVIDUAL_WARNINGS = 20

T = TypeVar("T")
R = TypeVar("R")
ProgressFn = Callable[[str, dict[str, Any]], None]


class IngestError(RuntimeError):
    """Ingestion cannot proceed safely (e.g. store built with another key or model)."""


@dataclass
class IngestReport:
    run_id: str
    data_dir: str
    dry_run: bool = False
    full_rebuild: bool = False
    files_seen: int = 0
    indexed_new: int = 0
    indexed_changed: int = 0
    unchanged: int = 0
    rejected: int = 0
    failed: int = 0
    skipped_unsupported: int = 0
    deleted_docs: int = 0
    chunks_written: int = 0
    chunks_deleted: int = 0
    injection_flagged_chunks: int = 0
    sparse_partitions_rebuilt: int = 0
    max_chunk_tokens: int = 0
    elapsed_s: float = 0.0
    rejected_examples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Candidate:
    path: Path
    rel: str
    doc_id: str
    prev_fingerprint: str | None


@dataclass
class _Prepared:
    cand: _Candidate
    status: str  # new | changed | unchanged | rejected | failed
    fingerprint: str | None = None
    labels: DocLabels | None = None
    chunks: list[TextChunk] = field(default_factory=list)
    error: str | None = None


def doc_id_for(rel_path: str) -> str:
    return hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:32]


def _bounded_map(fn: Callable[[T], R], items: Iterable[T], workers: int, window: int) -> Iterator[R]:
    """Ordered map that never holds more than ``window`` results in flight."""
    if workers <= 1:
        for item in items:
            yield fn(item)
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: deque = deque()
        for item in items:
            pending.append(pool.submit(fn, item))
            if len(pending) >= window:
                yield pending.popleft().result()
        while pending:
            yield pending.popleft().result()


class IngestPipeline:
    def __init__(
        self,
        settings: Settings,
        encryptor: VectorStoreEncryptor,
        store: VectorStore | None,
        state: IngestState | None,
        encoder: Any = None,
        length_fn: LengthFn | None = None,
        sparse_dir: str | Path | None = None,
    ):
        self.settings = settings
        self.encryptor = encryptor
        self.store = store
        self.state = state
        self.encoder = encoder
        self.index_key = encryptor.derive_subkey(INDEX_KEY_LABEL)
        self.sparse_dir = Path(sparse_dir) if sparse_dir else None
        self.hasher = sparse.TermHasher(encryptor.derive_subkey(sparse.SPARSE_KEY_LABEL))
        self._touched: set[str] = set()

        if length_fn is None:
            tokenizer = getattr(encoder, "tokenizer", None)
            length_fn = token_length_fn(tokenizer) if tokenizer is not None else whitespace_length
        self.length_fn = length_fn

        self.max_tokens = settings.chunk_size_tokens
        limit = model_max_tokens(encoder) if encoder is not None else None
        if limit is not None and limit < self.max_tokens:
            logger.warning("chunk_size_tokens=%d exceeds what %s embeds (%d tokens); capping chunks at %d so no "
                           "text is silently truncated", self.max_tokens, settings.embed_model, limit, limit)
            self.max_tokens = limit
        self.overlap = min(settings.chunk_overlap_tokens, self.max_tokens // 2)

    # ------------------------------------------------------------------ public API
    def run(self, data_dir: str | Path, *, full_rebuild: bool = False, dry_run: bool = False, workers: int = 1,
            progress: ProgressFn | None = None, run_id: str | None = None) -> IngestReport:
        root = Path(data_dir).resolve()
        if not root.is_dir():
            raise IngestError(f"data dir does not exist: {root}")
        report = IngestReport(run_id=run_id or uuid.uuid4().hex[:12], data_dir=str(root), dry_run=dry_run,
                              full_rebuild=full_rebuild, max_chunk_tokens=self.max_tokens)
        started = time.perf_counter()
        self._touched = set()
        if not dry_run:
            if self.store is None or self.state is None:
                raise IngestError("store and state are required unless dry_run=True")
            if self.encoder is None:
                raise IngestError("an encoder is required unless dry_run=True")
            self.state.start_run(report.run_id)
            self._prepare_store(full_rebuild)

        try:
            self._ingest(root, report, dry_run=dry_run, full_rebuild=full_rebuild, workers=workers, progress=progress)
        except BaseException:
            report.elapsed_s = round(time.perf_counter() - started, 2)
            if not dry_run and self.state is not None:
                self.state.update_run(report.run_id, "failed", report.as_dict(), finished=True)
            raise
        report.elapsed_s = round(time.perf_counter() - started, 2)
        if not dry_run and self.state is not None:
            self.state.update_run(report.run_id, "completed", report.as_dict(), finished=True)
        logger.info("ingest %s finished: %s", report.run_id, {k: v for k, v in report.as_dict().items()
                                                              if k != "rejected_examples"})
        return report

    # ------------------------------------------------------------------ setup
    def _store_identity(self) -> dict[str, str]:
        return {"embed_model": self.settings.embed_model, "index_key_id": self.encryptor.key_id,
                "chunker": f"{CHUNKER_VERSION}:{self.max_tokens}:{self.overlap}"}

    def _prepare_store(self, full_rebuild: bool) -> None:
        assert self.store is not None and self.state is not None
        if full_rebuild:
            logger.warning("full rebuild: wiping vector store and ingestion state")
            self.store.reset()
            self.state.reset()
            if self.sparse_dir is not None:
                sparse.wipe(self.sparse_dir)

        identity = self._store_identity()
        info = self.store.get_info()
        if self.store.count() > 0:
            for key in ("embed_model", "index_key_id"):
                if info.get(key) and info[key] != identity[key]:
                    raise IngestError(
                        f"vector store was built with {key}={info[key]!r} but the current setting is "
                        f"{identity[key]!r}; run ingest with --full-rebuild to re-index")
        elif self.state.indexed_count() > 0:
            logger.warning("vector store is empty but the state DB lists indexed documents; resetting state")
            self.state.reset()

        # A state DB that was used with another store (or key) cannot be trusted for skip decisions.
        state_identity = f"{self.store.backend}|{identity['index_key_id']}|{identity['embed_model']}"
        if self.state.get_meta("store_identity") not in (None, state_identity):
            logger.warning("state DB belongs to a different store/key/model; resetting state")
            self.state.reset()
        self.state.set_meta("store_identity", state_identity)
        self.store.set_info(identity)

    # ------------------------------------------------------------------ main loop
    def _discover(self, root: Path, report: IngestReport) -> Iterator[Path]:
        supported = supported_extensions()
        warned = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file() or any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            if is_sidecar(path) or path.name in _IGNORED_NAMES:
                continue
            if path.suffix.lower() not in supported:
                report.skipped_unsupported += 1
                if warned < _MAX_INDIVIDUAL_WARNINGS:
                    logger.warning("skipping unsupported file type: %s", path.relative_to(root).as_posix())
                    warned += 1
                continue
            yield path

    def _candidates(self, root: Path, report: IngestReport, full_rebuild: bool) -> Iterator[_Candidate]:
        for path in self._discover(root, report):
            rel = path.relative_to(root).as_posix()
            doc_id = doc_id_for(rel)
            prev = None
            if self.state is not None and not full_rebuild:
                st = self.state.get(doc_id)
                if st is not None and st.status == STATUS_INDEXED:
                    prev = st.file_hash
            yield _Candidate(path=path, rel=rel, doc_id=doc_id, prev_fingerprint=prev)

    def _prepare(self, resolver: MetadataResolver, cand: _Candidate) -> _Prepared:
        try:
            labels = resolver.resolve(cand.path)
        except MetadataError as exc:
            return _Prepared(cand, STATUS_REJECTED, error=str(exc))
        try:
            raw = cand.path.read_bytes()
        except OSError as exc:
            return _Prepared(cand, STATUS_FAILED, error=f"read failed: {exc}")
        fingerprint = keyed_hash(self.index_key, hashlib.sha256(raw).hexdigest(), labels.department,
                                 labels.clearance, self.settings.embed_model, self.settings.embed_doc_prefix,
                                 f"{CHUNKER_VERSION}:{self.max_tokens}:{self.overlap}")
        if fingerprint == cand.prev_fingerprint:
            return _Prepared(cand, "unchanged", fingerprint=fingerprint, labels=labels)
        try:
            text = load_file(cand.path)
        except LoaderError as exc:
            return _Prepared(cand, STATUS_FAILED, fingerprint=fingerprint, error=str(exc))
        chunks = chunk_text(text, self.max_tokens, self.overlap, self.length_fn)
        status = "changed" if cand.prev_fingerprint else "new"
        return _Prepared(cand, status, fingerprint=fingerprint, labels=labels, chunks=chunks)

    def _ingest(self, root: Path, report: IngestReport, *, dry_run: bool, full_rebuild: bool, workers: int,
                progress: ProgressFn | None) -> None:
        resolver = MetadataResolver(root)
        root_key = str(root)
        seen: set[str] = set()
        buffer: list[_Prepared] = []
        buffered_chunks = 0

        prepared_iter = _bounded_map(lambda c: self._prepare(resolver, c),
                                     self._candidates(root, report, full_rebuild), workers, window=max(4, workers * 4))
        for prep in prepared_iter:
            report.files_seen += 1
            seen.add(prep.cand.doc_id)
            if prep.status == "unchanged":
                report.unchanged += 1
            elif prep.status in (STATUS_REJECTED, STATUS_FAILED):
                self._unindex(prep, root_key, report, dry_run)
            else:
                if prep.status == "new":
                    report.indexed_new += 1
                else:
                    report.indexed_changed += 1
                if dry_run:
                    report.chunks_written += len(prep.chunks)
                else:
                    buffer.append(prep)
                    buffered_chunks += len(prep.chunks)
                    if buffered_chunks >= self.settings.ingest_batch_size:
                        self._flush(buffer, root_key, report)
                        buffer, buffered_chunks = [], 0
                        if self.state is not None:
                            self.state.update_run(report.run_id, "running", report.as_dict())
            if progress:
                progress("file", {"path": prep.cand.rel, "status": prep.status})
        if buffer:
            self._flush(buffer, root_key, report)

        self._delete_missing(root_key, seen, report, dry_run)
        if not dry_run:
            self._rebuild_sparse(report)

    def _note_partition(self, doc_id: str) -> None:
        """Remember the partition a document currently lives in (before its state changes)."""
        prev = self.state.get(doc_id) if self.state is not None else None
        if prev is not None and prev.department and prev.clearance:
            self._touched.add(sparse.partition_name(prev.department, prev.clearance))

    def _rebuild_sparse(self, report: IngestReport) -> None:
        if self.sparse_dir is None or self.state is None:
            return
        on_disk = set(sparse.SparseRetriever(self.sparse_dir, self.hasher).available_partitions())
        # Also rebuild partitions the state knows about but whose index is missing (e.g. deleted by hand).
        todo = self._touched | (self.state.partitions() - on_disk)
        if todo:
            counts = sparse.rebuild_partitions(self.sparse_dir, todo, self.state.partition_terms)
            report.sparse_partitions_rebuilt = len(counts)
        self._touched = set()

    # ------------------------------------------------------------------ writes
    def _flush(self, docs: list[_Prepared], root_key: str, report: IngestReport) -> None:
        assert self.store is not None and self.state is not None
        rows = [(doc, chunk) for doc in docs for chunk in doc.chunks]
        if rows:
            texts = [self.settings.embed_doc_prefix + chunk.text for _, chunk in rows]
            embeddings = self.encoder.encode(texts, batch_size=self.settings.embed_batch_size,
                                             normalize_embeddings=True, convert_to_numpy=True,
                                             show_progress_bar=False)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            key_id = self.encryptor.key_id
            ids, payloads, metadatas = [], [], []
            for doc, chunk in rows:
                assert doc.labels is not None
                ids.append(keyed_hash(self.index_key, doc.cand.doc_id, str(chunk.index), chunk.text))
                payloads.append(self.encryptor.encrypt(chunk.text))
                flagged = bool(flag_suspicious(chunk.text))
                report.injection_flagged_chunks += int(flagged)
                metadatas.append({
                    "doc_id": doc.cand.doc_id,
                    "chunk_index": chunk.index,
                    "content_hash": keyed_hash(self.index_key, chunk.text),
                    "source": doc.cand.path.name,
                    "path": doc.cand.rel,
                    "department": doc.labels.department,
                    "clearance": doc.labels.clearance,
                    "key_id": key_id,
                    "embed_model": self.settings.embed_model,
                    "ingested_at": now,
                    "injection_flagged": flagged,
                    "char_start": chunk.start,
                    "char_end": chunk.end,
                    "tokens": chunk.tokens,
                })
            # Record ownership of the new IDs before writing them, so a crash can never orphan chunks.
            new_ids_by_doc: dict[str, list[str]] = {}
            claims = []
            for (doc, chunk), cid in zip(rows, ids):
                assert doc.labels is not None
                new_ids_by_doc.setdefault(doc.cand.doc_id, []).append(cid)
                claims.append((cid, doc.cand.doc_id, chunk.index, key_id,
                               sparse.partition_name(doc.labels.department, doc.labels.clearance),
                               self.hasher.encode_text(chunk.text)))
            self.state.claim_chunks(claims)
            self.store.upsert(ids, embeddings, payloads, metadatas)
            report.chunks_written += len(ids)
        else:
            new_ids_by_doc = {}

        for doc in docs:
            assert doc.labels is not None and doc.fingerprint is not None
            self._note_partition(doc.cand.doc_id)
            self._touched.add(sparse.partition_name(doc.labels.department, doc.labels.clearance))
            new_ids = new_ids_by_doc.get(doc.cand.doc_id, [])
            keep = set(new_ids)
            stale = [cid for cid in self.state.chunk_ids(doc.cand.doc_id) if cid not in keep]
            if stale:
                self.store.delete(stale)
                report.chunks_deleted += len(stale)
            self.state.mark_indexed(
                doc_id=doc.cand.doc_id, root=root_key, path=doc.cand.rel, file_hash=doc.fingerprint,
                department=doc.labels.department, clearance=doc.labels.clearance, chunk_ids=new_ids)

    def _unindex(self, prep: _Prepared, root_key: str, report: IngestReport, dry_run: bool) -> None:
        if prep.status == STATUS_REJECTED:
            report.rejected += 1
            if len(report.rejected_examples) < _MAX_INDIVIDUAL_WARNINGS:
                report.rejected_examples.append(prep.error or prep.cand.rel)
            logger.warning("REJECTED (fail closed): %s", prep.error)
        else:
            report.failed += 1
            logger.warning("FAILED: %s: %s", prep.cand.rel, prep.error)
        if dry_run or self.state is None or self.store is None:
            return
        # A previously indexed document that is now unlabelled or unreadable must disappear.
        self._note_partition(prep.cand.doc_id)
        old = self.state.chunk_ids(prep.cand.doc_id)
        if old:
            self.store.delete(old)
            report.chunks_deleted += len(old)
        self.state.mark_unindexed(doc_id=prep.cand.doc_id, root=root_key, path=prep.cand.rel, status=prep.status,
                                  error=prep.error or "", file_hash=prep.fingerprint)

    def _delete_missing(self, root_key: str, seen: set[str], report: IngestReport, dry_run: bool) -> None:
        if self.state is None:
            return
        for doc_id, rel in self.state.docs_under(root_key):
            if doc_id in seen:
                continue
            report.deleted_docs += 1
            old = self.state.chunk_ids(doc_id)
            report.chunks_deleted += len(old)
            logger.info("source removed, deleting %d chunks: %s", len(old), rel)
            if not dry_run and self.store is not None:
                self._note_partition(doc_id)
                if old:
                    self.store.delete(old)
                self.state.delete_doc(doc_id)

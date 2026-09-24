"""SQLite-backed ingestion state: which documents are indexed, with which fingerprint and chunks.

The state DB makes ingestion incremental and resumable. A document is marked ``indexed`` only
after all of its chunks have been written to the vector store, so a crash mid-run simply
re-processes the unfinished documents next time (chunk IDs are deterministic, so the
re-upsert is idempotent).
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    root        TEXT NOT NULL,
    path        TEXT NOT NULL,
    file_hash   TEXT,
    status      TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    department  TEXT,
    clearance   TEXT,
    error       TEXT,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS documents_root ON documents(root);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    key_id      TEXT NOT NULL,
    partition   TEXT,
    terms       BLOB
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS chunks_key ON chunks(key_id);
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  REAL NOT NULL,
    finished_at REAL,
    status      TEXT NOT NULL,
    stats       TEXT
);
"""

STATUS_INDEXED = "indexed"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class DocState:
    doc_id: str
    root: str
    path: str
    file_hash: str | None
    status: str
    chunk_count: int
    department: str | None
    clearance: str | None


class IngestState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.execute("CREATE INDEX IF NOT EXISTS chunks_partition ON chunks(partition)")
        self._conn.commit()

    def _migrate(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(chunks)")}
        for name, decl in (("partition", "TEXT"), ("terms", "BLOB")):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE chunks ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    # -- meta -----------------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as c:
            c.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, value))

    # -- documents ------------------------------------------------------------------
    def get(self, doc_id: str) -> DocState | None:
        row = self._conn.execute(
            "SELECT doc_id, root, path, file_hash, status, chunk_count, department, clearance "
            "FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        return DocState(*row) if row else None

    def chunk_ids(self, doc_id: str) -> list[str]:
        rows = self._conn.execute("SELECT chunk_id FROM chunks WHERE doc_id = ? ORDER BY chunk_index", (doc_id,))
        return [r[0] for r in rows]

    def docs_under(self, root: str) -> list[tuple[str, str]]:
        """(doc_id, path) for every document recorded for ``root``."""
        return list(self._conn.execute("SELECT doc_id, path FROM documents WHERE root = ?", (root,)))

    def mark_indexed(self, *, doc_id: str, root: str, path: str, file_hash: str, department: str,
                     clearance: str, chunk_ids: list[str]) -> None:
        """Record a fully written document whose current chunks are ``chunk_ids`` (already claimed).

        Any other chunk rows the document still owns (stale versions) are dropped.
        """
        keep = set(chunk_ids)
        with self.transaction() as c:
            owned = [r[0] for r in c.execute("SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,))]
            c.executemany("DELETE FROM chunks WHERE chunk_id = ?", [(cid,) for cid in owned if cid not in keep])
            self._upsert_doc(c, doc_id, root, path, file_hash, STATUS_INDEXED, len(chunk_ids), department, clearance,
                             None)

    def claim_chunks(self, rows: list[tuple[str, str, int, str, str, bytes]]) -> None:
        """Record (chunk_id, doc_id, chunk_index, key_id, partition, terms) before writing to the store.

        Until ``mark_indexed`` runs, a document owns the union of its old and new chunks, so a crash
        between the store write and ``mark_indexed`` never leaves chunks the state DB doesn't know.
        """
        with self.transaction() as c:
            c.executemany(
                "INSERT INTO chunks(chunk_id, doc_id, chunk_index, key_id, partition, terms) VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(chunk_id) DO UPDATE SET doc_id=excluded.doc_id, chunk_index=excluded.chunk_index, "
                "key_id=excluded.key_id, partition=excluded.partition, terms=excluded.terms", rows)

    def partition_terms(self, partition: str) -> Iterator[tuple[str, bytes]]:
        """(chunk_id, hashed-term blob) for every chunk in ``partition``, streamed."""
        cur = self._conn.execute("SELECT chunk_id, terms FROM chunks WHERE partition = ? ORDER BY chunk_id",
                                 (partition,))
        while True:
            rows = cur.fetchmany(2000)
            if not rows:
                return
            yield from rows

    def partitions(self) -> set[str]:
        return {r[0] for r in self._conn.execute("SELECT DISTINCT partition FROM chunks WHERE partition IS NOT NULL")}

    def mark_unindexed(self, *, doc_id: str, root: str, path: str, status: str, error: str,
                       file_hash: str | None = None) -> None:
        """Record a rejected/failed document; it owns no chunks."""
        with self.transaction() as c:
            c.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            self._upsert_doc(c, doc_id, root, path, file_hash, status, 0, None, None, error)

    def delete_doc(self, doc_id: str) -> None:
        with self.transaction() as c:
            c.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            c.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))

    @staticmethod
    def _upsert_doc(c: sqlite3.Connection, doc_id: str, root: str, path: str, file_hash: str | None, status: str,
                    chunk_count: int, department: str | None, clearance: str | None, error: str | None) -> None:
        c.execute(
            "INSERT INTO documents(doc_id, root, path, file_hash, status, chunk_count, department, clearance, error, "
            "updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(doc_id) DO UPDATE SET root=excluded.root, "
            "path=excluded.path, file_hash=excluded.file_hash, status=excluded.status, "
            "chunk_count=excluded.chunk_count, department=excluded.department, clearance=excluded.clearance, "
            "error=excluded.error, updated_at=excluded.updated_at",
            (doc_id, root, path, file_hash, status, chunk_count, department, clearance, error, time.time()))

    def counts(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT status, COUNT(*) FROM documents GROUP BY status")
        out = {status: n for status, n in rows}
        out["chunks"] = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return out

    def indexed_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM documents WHERE status = ?", (STATUS_INDEXED,)).fetchone()[0]

    def iter_chunks(self, batch_size: int = 1000, key_id_not: str | None = None) -> Iterator[list[tuple[str, str]]]:
        """Yield batches of (chunk_id, key_id), optionally only chunks not under ``key_id_not``."""
        last = ""
        while True:
            if key_id_not is None:
                rows = self._conn.execute(
                    "SELECT chunk_id, key_id FROM chunks WHERE chunk_id > ? ORDER BY chunk_id LIMIT ?",
                    (last, batch_size)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT chunk_id, key_id FROM chunks WHERE chunk_id > ? AND key_id != ? ORDER BY chunk_id LIMIT ?",
                    (last, key_id_not, batch_size)).fetchall()
            if not rows:
                return
            yield rows
            last = rows[-1][0]

    def set_chunk_key(self, chunk_ids: list[str], key_id: str) -> None:
        with self.transaction() as c:
            c.executemany("UPDATE chunks SET key_id = ? WHERE chunk_id = ?", [(key_id, cid) for cid in chunk_ids])

    # -- runs -----------------------------------------------------------------------
    def start_run(self, run_id: str) -> None:
        with self.transaction() as c:
            c.execute("INSERT INTO runs(run_id, started_at, status) VALUES(?, ?, 'running')", (run_id, time.time()))

    def update_run(self, run_id: str, status: str, stats: dict, finished: bool = False) -> None:
        with self.transaction() as c:
            c.execute("UPDATE runs SET status = ?, stats = ?, finished_at = ? WHERE run_id = ?",
                      (status, json.dumps(stats), time.time() if finished else None, run_id))

    def get_run(self, run_id: str) -> dict | None:
        row = self._conn.execute("SELECT run_id, started_at, finished_at, status, stats FROM runs WHERE run_id = ?",
                                 (run_id,)).fetchone()
        if not row:
            return None
        return {"run_id": row[0], "started_at": row[1], "finished_at": row[2], "status": row[3],
                "stats": json.loads(row[4]) if row[4] else {}}

    # -- maintenance ----------------------------------------------------------------
    def reset(self) -> None:
        """Forget all documents and chunks (used by --full-rebuild). Runs and meta are kept."""
        with self.transaction() as c:
            c.execute("DELETE FROM chunks")
            c.execute("DELETE FROM documents")

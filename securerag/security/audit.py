"""Tamper-evident, append-only audit log (hash-chained JSONL).

Each event carries ``seq``, ``prev_hash`` and ``entry_hash = sha256(canonical_json(event
without entry_hash))``, where ``prev_hash`` is the previous event's ``entry_hash`` (64 zeros for
the first event). Editing, deleting, reordering or inserting a line breaks the chain, and
``verify_chain`` / ``scripts/verify_audit.py`` report the first broken ``seq``.

* Writes take a cross-process file lock (``filelock``), so several API workers and ingestion jobs
  can share one log without interleaving or forking the chain.
* When the active file reaches ``max_bytes`` it is renamed to ``<stem>.<NNNNN><suffix>`` and a new
  file starts. The chain continues across files.
* Reading recent events seeks from the end of the file instead of loading it whole.
* Queries and answers are stored only as SHA-256 prefixes.

Tamper-*evident*, not tamper-proof: someone who can rewrite the whole log can recompute every
hash. Ship the log (or periodic ``entry_hash`` checkpoints) to write-once storage to anchor it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from filelock import FileLock

GENESIS_HASH = "0" * 64
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_TAIL_BLOCK = 8192


def _canonical(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_entry_hash(event: dict[str, Any]) -> str:
    body = {k: v for k, v in event.items() if k != "entry_hash"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _tail_lines(path: Path, n: int) -> list[str]:
    """Last ``n`` non-empty lines of ``path``, reading backwards in blocks."""
    if n <= 0 or not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        buf = b""
        while pos > 0 and buf.count(b"\n") <= n:
            step = min(_TAIL_BLOCK, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
    lines = [ln for ln in buf.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    return lines[-n:]


class AuditLog:
    def __init__(self, path: str | Path, max_bytes: int = DEFAULT_MAX_BYTES):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self._lock = FileLock(str(self.path) + ".lock")
        self._archive_re = re.compile(rf"^{re.escape(self.path.stem)}\.(\d{{5}}){re.escape(self.path.suffix)}$")

    # ------------------------------------------------------------------ files
    def archives(self) -> list[Path]:
        if not self.path.parent.exists():
            return []
        found = [(int(m.group(1)), p) for p in self.path.parent.iterdir() if (m := self._archive_re.match(p.name))]
        return [p for _, p in sorted(found)]

    def files(self) -> list[Path]:
        """All log files, oldest first."""
        return self.archives() + ([self.path] if self.path.exists() else [])

    def _last_event(self) -> dict[str, Any] | None:
        for f in reversed(self.files()):
            lines = _tail_lines(f, 1)
            if lines:
                return json.loads(lines[-1])
        return None

    def _rotate_if_needed(self) -> None:
        if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
            archives = self.archives()
            match = self._archive_re.match(archives[-1].name) if archives else None
            nxt = int(match.group(1)) + 1 if match else 1
            self.path.rename(self.path.with_name(f"{self.path.stem}.{nxt:05d}{self.path.suffix}"))

    # ------------------------------------------------------------------ API
    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            last = self._last_event()
            if last is not None and "entry_hash" not in last:
                # Pre-chain log from an older version: set it aside and start a fresh chain.
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                self.path.rename(self.path.with_name(f"{self.path.stem}.legacy-{stamp}{self.path.suffix}"))
                last = None
            self._rotate_if_needed()
            record = dict(event)
            record["seq"] = (last["seq"] + 1) if last else 1
            record["prev_hash"] = last["entry_hash"] if last else GENESIS_HASH
            record["entry_hash"] = compute_entry_hash(record)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return record

    def read_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Most recent ``limit`` events, newest first."""
        out: list[dict[str, Any]] = []
        for f in reversed(self.files()):
            for line in reversed(_tail_lines(f, limit - len(out))):
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            if len(out) >= limit:
                break
        return out[:limit]

    def iter_events(self) -> Iterator[tuple[Path, int, str]]:
        for f in self.files():
            with f.open(encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if line.strip():
                        yield f, lineno, line


def verify_chain(path: str | Path) -> dict[str, Any]:
    """Walk every file of the log in order and check the hash chain.

    Returns {"ok": bool, "events": n, "files": k, "error": {...} | None, "head": last entry_hash}.
    """
    log = AuditLog(path)
    files = len(log.files())
    prev_hash, expected_seq, count = GENESIS_HASH, 1, 0

    def fail(where: dict[str, Any], reason: str) -> dict[str, Any]:
        return {"ok": False, "events": count, "files": files, "error": {**where, "reason": reason}, "head": prev_hash}

    for f, lineno, line in log.iter_events():
        where = {"file": f.name, "line": lineno, "seq": expected_seq}
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return fail(where, "not JSON")
        if event.get("seq") != expected_seq:
            return fail(where, f"sequence gap: expected {expected_seq}, found {event.get('seq')}")
        if event.get("prev_hash") != prev_hash:
            return fail(where, "prev_hash does not match previous entry (deleted/reordered line)")
        if compute_entry_hash(event) != event.get("entry_hash"):
            return fail(where, "entry_hash mismatch (line was modified)")
        prev_hash, expected_seq, count = event["entry_hash"], expected_seq + 1, count + 1
    return {"ok": True, "events": count, "files": files, "error": None, "head": prev_hash}


# ---------------------------------------------------------------------- module-level helpers

_LOGS: dict[str, AuditLog] = {}
_LOGS_LOCK = threading.Lock()


def get_audit_log(path: str | Path, max_bytes: int | None = None) -> AuditLog:
    key = str(Path(path).resolve())
    with _LOGS_LOCK:
        log = _LOGS.get(key)
        if log is None:
            limit = max_bytes or int(os.getenv("AUDIT_MAX_BYTES", DEFAULT_MAX_BYTES))
            log = _LOGS[key] = AuditLog(path, limit)
        return log


def audit_event(
    path: str,
    *,
    request_id: str,
    user_role: str,
    query: str,
    retrieved_ids: list[str],
    authorized_count: int,
    blocked_count: int,
    flagged_count: int,
    answer: str,
    provider: str,
    latency_ms: float,
    status: str = "success",
    principal_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """
    Appends a hash-chained structured audit entry.
    Queries and answers are stored as SHA-256 hashes to preserve privacy while ensuring auditability.
    """
    now = datetime.now(timezone.utc)
    event = {
        "timestamp": now.timestamp(),
        "timestamp_iso": now.isoformat(),
        "request_id": request_id,
        "principal_id": principal_id or "unknown",
        "user_role": user_role,
        "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
        "retrieved_chunk_ids": retrieved_ids,
        "num_retrieved": len(retrieved_ids),
        "authorized_count": authorized_count,
        "blocked_count": blocked_count,
        "flagged_chunks": flagged_count,
        "answer_hash": hashlib.sha256(answer.encode("utf-8")).hexdigest()[:16],
        "provider": provider,
        "latency_ms": round(latency_ms, 2),
        "status": status,
        **extra,
    }
    return get_audit_log(path).append(event)


def read_recent_audit_events(path: str, limit: int = 50) -> list[dict[str, Any]]:
    """
    Reads recent audit events in reverse chronological order, seeking from the end of the log.
    """
    try:
        return get_audit_log(path).read_recent(limit)
    except OSError:
        return []

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
) -> dict[str, Any]:
    """
    Appends an immutable structured audit log entry in JSONL format.
    Queries and answers are stored as SHA-256 hashes to preserve privacy while ensuring auditability.
    """
    now = datetime.now(timezone.utc)
    event = {
        "timestamp": now.timestamp(),
        "timestamp_iso": now.isoformat(),
        "request_id": request_id,
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
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def read_recent_audit_events(path: str, limit: int = 50) -> list[dict[str, Any]]:
    """
    Reads recent audit events from the specified JSONL path in reverse chronological order.
    """
    target = Path(path)
    if not target.exists():
        return []
    
    events = []
    try:
        with target.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception:
        return []

    return list(reversed(events[-limit:]))

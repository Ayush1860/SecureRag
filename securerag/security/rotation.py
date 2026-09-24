"""Re-encrypt stored chunks under the keyring's active key.

Resumable: the ingestion state DB records each chunk's key id, so an interrupted rotation picks
up where it stopped and already-rotated chunks are never touched twice. Vectors, chunk IDs and
the sparse index are unchanged (they depend on the index key, not the encryption key).
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Callable

from securerag.ingestion.state import IngestState
from securerag.retrieval.vector_store import VectorStore
from securerag.security.encryption import VectorStoreEncryptor, aad_for

logger = logging.getLogger(__name__)


@dataclass
class RotationReport:
    active_key_id: str
    rotated: int = 0
    upgraded_v1: int = 0
    missing: int = 0
    already_current: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def rotate_store(
    store: VectorStore,
    state: IngestState,
    encryptor: VectorStoreEncryptor,
    *,
    batch_size: int = 256,
    include_current: bool = False,
    progress: Callable[[int], None] | None = None,
) -> RotationReport:
    """Re-encrypt chunks not yet under the active key (or all chunks with ``include_current``,
    which also upgrades legacy v1 blobs to v2 + AAD)."""
    active = encryptor.key_id
    report = RotationReport(active_key_id=active)
    batches = state.iter_chunks(batch_size, key_id_not=None if include_current else active)
    for rows in batches:
        ids = [cid for cid, _ in rows]
        found = {cid: (ct, meta) for cid, ct, meta in store.get(ids)}
        new_ids, new_payloads, new_metas = [], [], []
        for cid in ids:
            if cid not in found:
                report.missing += 1
                continue
            ciphertext, meta = found[cid]
            is_v1 = VectorStoreEncryptor.blob_key_id(ciphertext) is None
            if not is_v1 and VectorStoreEncryptor.blob_key_id(ciphertext) == active:
                report.already_current += 1
                continue
            aad = aad_for(cid, meta)
            plaintext = encryptor.decrypt(ciphertext, aad=None if is_v1 else aad)
            new_ids.append(cid)
            new_payloads.append(encryptor.encrypt(plaintext, aad=aad))
            new_metas.append({**meta, "key_id": active})
            del plaintext
            if is_v1:
                report.upgraded_v1 += 1
            else:
                report.rotated += 1
        if new_ids:
            store.update_payloads(new_ids, new_payloads, new_metas)
        # Record progress only after the store write, so a crash re-processes (idempotently).
        state.set_chunk_key(ids, active)
        if progress:
            progress(len(ids))
    logger.info("rotation finished: %s", report.as_dict())
    return report

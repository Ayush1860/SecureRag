"""Vector store abstraction.

Payloads are always AES-GCM ciphertext; the store never sees plaintext. Metadata carries the
RBAC labels used for pre-filtering. ``where`` filters use the Chroma operator subset
(``$and``, ``$in``, equality) produced by ``securerag.security.rbac.build_chroma_filter``;
other backends translate it.
"""
from __future__ import annotations

import logging
import random
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from securerag.security.rbac import all_partitions, metadata_matches_filter, partition_name, partitions_for_filter

logger = logging.getLogger(__name__)

COLLECTION_NAME = "securerag_chunks"


@runtime_checkable
class VectorStore(Protocol):
    backend: str

    def upsert(self, ids: Sequence[str], embeddings: Sequence[Sequence[float]], payloads: Sequence[str],
               metadatas: Sequence[dict[str, Any]]) -> None: ...

    def update_payloads(self, ids: Sequence[str], payloads: Sequence[str],
                        metadatas: Sequence[dict[str, Any]]) -> None:
        """Replace ciphertext + metadata of existing chunks, keeping their vectors (key rotation)."""
        ...

    def delete(self, ids: Sequence[str]) -> None: ...

    def query(self, embedding: Sequence[float], n: int,
              where: dict[str, Any] | None = None) -> list[tuple[str, float]]: ...

    def get(self, ids: Sequence[str]) -> list[tuple[str, str, dict[str, Any]]]: ...

    def get_metadata(self, ids: Sequence[str]) -> list[tuple[str, dict[str, Any]]]: ...

    def count(self) -> int: ...

    def sample_ids(self, n: int) -> list[str]: ...

    def iter_all(self, batch_size: int = 1000) -> Iterator[list[tuple[str, str, dict[str, Any]]]]: ...

    def get_info(self) -> dict[str, Any]: ...

    def set_info(self, info: dict[str, Any]) -> None: ...

    def reset(self) -> None: ...


def _batches(n: int, size: int) -> Iterator[tuple[int, int]]:
    for start in range(0, n, size):
        yield start, min(start + size, n)


class ChromaVectorStore:
    """Chroma-backed store (single collection). All writes are split to respect
    ``client.get_max_batch_size()``.

    The RBAC pre-filter is sent as one ``partition $in [...]`` condition on a dedicated metadata
    field; Chroma evaluates it faster than the equivalent ``$and`` of two ``$in`` clauses.
    Chroma still scans matching metadata for filtered queries, so for large corpora use the
    Qdrant backend (payload-indexed filtered HNSW).

    A one-collection-per-partition layout was tried and dropped: with ~15 collections open in one
    process, chromadb 1.5.x intermittently fails queries with "Error creating hnsw segment reader:
    Nothing found on disk" (see docs/SCALE_WORKLOG.md, Phase 2).
    """

    backend = "chroma"

    def __init__(self, path: str | Path | None = None, collection_name: str = COLLECTION_NAME, client: Any = None):
        import chromadb

        if client is None:
            if path is None:
                raise ValueError("ChromaVectorStore needs a path or a client")
            Path(path).mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(path))
        self.client = client
        self.collection_name = collection_name
        self.collection = client.get_or_create_collection(collection_name)
        try:
            self.max_batch = int(client.get_max_batch_size())
        except Exception:  # noqa: BLE001 - older clients
            self.max_batch = 5000

    def _write(self, ids: list[str], embeddings: list[list[float]], payloads: list[str],
               metadatas: list[dict[str, Any]]) -> None:
        self.collection.upsert(ids=ids, embeddings=embeddings, documents=payloads, metadatas=metadatas)

    def upsert(self, ids: Sequence[str], embeddings: Sequence[Sequence[float]], payloads: Sequence[str],
               metadatas: Sequence[dict[str, Any]]) -> None:
        if not (len(ids) == len(embeddings) == len(payloads) == len(metadatas)):
            raise ValueError("upsert arguments must have equal length")
        for s, e in _batches(len(ids), self.max_batch):
            metas = [{**m, "partition": partition_name(m["department"], m["clearance"])} for m in metadatas[s:e]]
            self._write(list(ids[s:e]), [list(map(float, v)) for v in embeddings[s:e]], list(payloads[s:e]), metas)

    def update_payloads(self, ids: Sequence[str], payloads: Sequence[str],
                        metadatas: Sequence[dict[str, Any]]) -> None:
        for s, e in _batches(len(ids), self.max_batch):
            batch_ids = list(ids[s:e])
            # Pass the stored vectors back explicitly: given documents without embeddings, Chroma would
            # run its default embedding function over the ciphertext.
            current = self.collection.get(ids=batch_ids, include=["embeddings"])
            vectors = dict(zip(current["ids"], current["embeddings"]))
            keep = [i for i, cid in enumerate(batch_ids) if cid in vectors]
            metas = [{**metadatas[s + i], "partition": partition_name(metadatas[s + i]["department"],
                                                                       metadatas[s + i]["clearance"])} for i in keep]
            self.collection.update(ids=[batch_ids[i] for i in keep],
                                   embeddings=[list(map(float, vectors[batch_ids[i]])) for i in keep],
                                   documents=[payloads[s + i] for i in keep], metadatas=metas)

    def delete(self, ids: Sequence[str]) -> None:
        for s, e in _batches(len(ids), self.max_batch):
            self.collection.delete(ids=list(ids[s:e]))

    def query(self, embedding: Sequence[float], n: int, where: dict[str, Any] | None = None) -> list[tuple[str, float]]:
        n = min(n, self.count())
        if n <= 0:
            return []
        kwargs: dict[str, Any] = {"query_embeddings": [list(embedding)], "n_results": n,
                                  "include": ["distances", "metadatas"]}
        if where:
            partitions = partitions_for_filter(where)
            if not partitions:
                return []  # filter not understood: fail closed
            if set(partitions) != set(all_partitions()):
                # A filter allowing every partition is a no-op; Chroma would still scan for it.
                kwargs["where"] = ({"partition": {"$in": partitions}} if len(partitions) > 1
                                   else {"partition": partitions[0]})
        res = self.collection.query(**kwargs)
        ids = res.get("ids", [[]])[0]
        dists = (res.get("distances") or [[]])[0] or [0.0] * len(ids)
        metas = (res.get("metadatas") or [[]])[0] or [{}] * len(ids)
        # Smaller distance is better; expose a "higher is better" score. Re-check the original filter.
        return [(cid, -float(d)) for cid, d, m in zip(ids, dists, metas) if metadata_matches_filter(m or {}, where)]

    def get(self, ids: Sequence[str]) -> list[tuple[str, str, dict[str, Any]]]:
        if not ids:
            return []
        res = self.collection.get(ids=list(ids), include=["documents", "metadatas"])
        found = {cid: (doc, meta or {}) for cid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"])}
        return [(cid, *found[cid]) for cid in ids if cid in found]

    def get_metadata(self, ids: Sequence[str]) -> list[tuple[str, dict[str, Any]]]:
        if not ids:
            return []
        res = self.collection.get(ids=list(ids), include=["metadatas"])
        found = {cid: meta or {} for cid, meta in zip(res["ids"], res["metadatas"])}
        return [(cid, found[cid]) for cid in ids if cid in found]

    def count(self) -> int:
        return self.collection.count()

    def sample_ids(self, n: int) -> list[str]:
        offset = random.randrange(0, max(1, self.count() - n + 1))
        return list(self.collection.get(limit=n, offset=offset, include=[])["ids"])

    def iter_all(self, batch_size: int = 1000) -> Iterator[list[tuple[str, str, dict[str, Any]]]]:
        offset = 0
        while True:
            res = self.collection.get(limit=batch_size, offset=offset, include=["documents", "metadatas"])
            if not res["ids"]:
                return
            yield [(cid, doc, meta or {}) for cid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"])]
            offset += len(res["ids"])

    def get_info(self) -> dict[str, Any]:
        meta = self.collection.metadata or {}
        return {k[len("securerag:"):]: v for k, v in meta.items() if k.startswith("securerag:")}

    def set_info(self, info: dict[str, Any]) -> None:
        current = dict(self.collection.metadata or {})
        current.update({f"securerag:{k}": v for k, v in info.items()})
        # hnsw:* keys cannot be changed after creation; only pass ours.
        self.collection.modify(metadata={k: v for k, v in current.items() if not k.startswith("hnsw:")})

    def reset(self) -> None:
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:  # noqa: BLE001 - collection may not exist
            pass
        self.collection = self.client.get_or_create_collection(self.collection_name)

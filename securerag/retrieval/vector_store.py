"""Vector store abstraction.

Payloads are always AES-GCM ciphertext; the store never sees plaintext. Metadata carries the
RBAC labels used for pre-filtering. ``where`` filters use the Chroma operator subset
(``$and``, ``$in``, equality) produced by ``securerag.security.rbac.build_chroma_filter``;
other backends translate it.
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from securerag.security.rbac import (
    ID_SCHEME,
    all_partitions,
    metadata_matches_filter,
    partition_code,
    partition_name,
    partitions_for_filter,
)

logger = logging.getLogger(__name__)

COLLECTION_NAME = "securerag_chunks"
_OVERFETCH_MAX = 8000
_OVERFETCH_MIN_FRACTION = 0.15
_INFO_TTL_S = 30.0


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

    def __init__(self, path: str | Path | None = None, collection_name: str = COLLECTION_NAME, client: Any = None,
                 prefilter: str = "overfetch"):
        import chromadb

        if client is None:
            if path is None:
                raise ValueError("ChromaVectorStore needs a path or a client")
            Path(path).mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(path))
        if prefilter not in ("overfetch", "strict"):
            raise ValueError("prefilter must be 'overfetch' or 'strict'")
        self.prefilter = prefilter
        self._info_cache: tuple[float, dict[str, Any]] | None = None
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
        """Top ``n`` chunks allowed by ``where``; nothing outside ``where`` is ever returned.

        Chroma evaluates metadata filters by scanning every matching row, so a filtered query costs
        O(rows the role may see), dominated by metadata reads (~0.09 ms/row). ``prefilter="overfetch"``
        (default, needs the partition-prefixed ID scheme) asks the HNSW index for the unfiltered top
        k IDs + distances (k = max(20n, 1000), then 8000), keeps IDs whose partition prefix is
        allowed, and reads metadata for the final n only, re-checking it. When too few allowed IDs
        come back it falls back to the exact filtered query. Unauthorized IDs never leave this
        method and no payload is read. ``prefilter="strict"`` always sends the filter to Chroma.
        """
        total = self.count()
        n = min(n, total)
        if n <= 0:
            return []
        emb = [list(embedding)]
        if not where:
            return self._query(emb, n, None, None)
        partitions = partitions_for_filter(where)
        if not partitions:
            return []  # filter not understood: fail closed
        if set(partitions) == set(all_partitions()):
            # A filter allowing every partition is a no-op; Chroma would still scan for it.
            return self._query(emb, n, None, where)
        info = self._cached_info()
        fraction = self._allowed_fraction(info, partitions)
        # Strict filtering costs ~O(allowed rows): cheap when a role sees a small slice. Over-fetch
        # costs ~O(k = n / fraction): cheap when it sees a large one.
        use_overfetch = (self.prefilter == "overfetch" and info.get("id_scheme") == ID_SCHEME
                         and (fraction is None or fraction >= _OVERFETCH_MIN_FRACTION))
        if use_overfetch:
            allowed = {partition_code(p) for p in partitions}
            schedule = ((max(n * 20, 1000), _OVERFETCH_MAX) if fraction is None
                        else (math.ceil(4 * n / fraction), math.ceil(16 * n / fraction), _OVERFETCH_MAX))
            for k in schedule:
                k = min(total, k)
                # IDs + distances only (HNSW, ~10 ms for k=2000); metadata is fetched for the
                # final n only and re-checked, so a mis-prefixed ID can never slip through.
                res = self.collection.query(query_embeddings=emb, n_results=k, include=["distances"])
                ranked = [(cid, -float(d)) for cid, d in zip(res["ids"][0], res["distances"][0])
                          if cid[:4] in allowed]
                if len(ranked) >= n or k >= total:
                    top = ranked[:n]
                    metas = dict(self.get_metadata([cid for cid, _ in top]))
                    checked = [(cid, s) for cid, s in top if metadata_matches_filter(metas.get(cid, {}), where)]
                    if len(checked) >= min(n, len(ranked)):
                        return checked
                    break  # IDs and metadata disagree: use the exact filtered path
                if k >= _OVERFETCH_MAX:
                    break
        chroma_where = {"partition": {"$in": partitions}} if len(partitions) > 1 else {"partition": partitions[0]}
        return self._query(emb, n, chroma_where, where)

    def _query(self, emb: list[list[float]], k: int, chroma_where: dict[str, Any] | None,
               where: dict[str, Any] | None) -> list[tuple[str, float]]:
        kwargs: dict[str, Any] = {"query_embeddings": emb, "n_results": k, "include": ["distances", "metadatas"]}
        if chroma_where:
            kwargs["where"] = chroma_where
        res = self.collection.query(**kwargs)
        ids = res.get("ids", [[]])[0]
        dists = (res.get("distances") or [[]])[0] or [0.0] * len(ids)
        metas = (res.get("metadatas") or [[]])[0] or [{}] * len(ids)
        # Smaller distance is better; expose a "higher is better" score. Always re-check the filter.
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

    def _cached_info(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._info_cache is None or now - self._info_cache[0] > _INFO_TTL_S:
            self._info_cache = (now, self.get_info())
        return self._info_cache[1]

    @staticmethod
    def _allowed_fraction(info: dict[str, Any], partitions: list[str]) -> float | None:
        try:
            counts = json.loads(info.get("partition_counts") or "")
        except (TypeError, ValueError):
            return None
        total = sum(counts.values())
        return sum(counts.get(p, 0) for p in partitions) / total if total else None

    def get_info(self) -> dict[str, Any]:
        meta = self.collection.metadata or {}
        return {k[len("securerag:"):]: v for k, v in meta.items() if k.startswith("securerag:")}

    def set_info(self, info: dict[str, Any]) -> None:
        self._info_cache = None
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

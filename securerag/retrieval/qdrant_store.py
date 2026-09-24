"""Qdrant-backed ``VectorStore`` (optional extra: ``pip install qdrant-client``).

Use for scale: payload indexes on the RBAC fields make filtered search sublinear, and a
Qdrant server is safe for multiple API workers and ingestion jobs at once. Local mode
(``QDRANT_PATH``) and in-memory mode (``:memory:``, tests) are single-process.

Chunk IDs are 32-hex keyed hashes, i.e. 128 bits, so they map 1:1 onto Qdrant UUID point IDs.
The original ID is also kept in the payload (``cid``); the ciphertext lives in ``ct``.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Iterator, Sequence

logger = logging.getLogger(__name__)

_ID_NAMESPACE = uuid.UUID("5b0e6f3e-6d0c-4b8e-9a39-3c2f3f2d6a10")
_UPSERT_BATCH = 256
_INDEXED_FIELDS = ("department", "clearance", "doc_id")
_RESERVED = {"cid", "ct"}


def point_id(chunk_id: str) -> str:
    try:
        return str(uuid.UUID(hex=chunk_id))
    except ValueError:
        return str(uuid.uuid5(_ID_NAMESPACE, chunk_id))


def translate_filter(where: dict[str, Any] | None) -> Any:
    """Chroma-style filter -> Qdrant ``Filter``. Unsupported operators raise (fail closed)."""
    if not where:
        return None
    from qdrant_client import models

    must: list[Any] = []
    for cond in where.get("$and", [where]) if isinstance(where, dict) else []:
        if not isinstance(cond, dict):
            raise ValueError(f"unsupported filter clause: {cond!r}")
        for field, value in cond.items():
            if field.startswith("$"):
                raise ValueError(f"unsupported filter operator: {field}")
            if isinstance(value, dict):
                if set(value) == {"$in"}:
                    must.append(models.FieldCondition(key=field, match=models.MatchAny(any=list(value["$in"]))))
                elif set(value) == {"$eq"}:
                    must.append(models.FieldCondition(key=field, match=models.MatchValue(value=value["$eq"])))
                else:
                    raise ValueError(f"unsupported filter operator in {value!r}")
            else:
                must.append(models.FieldCondition(key=field, match=models.MatchValue(value=value)))
    return models.Filter(must=must)


class QdrantVectorStore:
    backend = "qdrant"

    def __init__(self, *, url: str | None = None, path: str | None = None, api_key: str | None = None,
                 location: str | None = None, collection_name: str = "securerag_chunks", client: Any = None):
        from qdrant_client import QdrantClient

        if client is None:
            if url:
                client = QdrantClient(url=url, api_key=api_key)
            elif location:
                client = QdrantClient(location=location)
            elif path:
                client = QdrantClient(path=path)
            else:
                raise ValueError("QdrantVectorStore needs url, path, location or client")
        self.client = client
        self.collection_name = collection_name
        self.meta_collection = f"{collection_name}__meta"

    # ------------------------------------------------------------------ helpers
    def _exists(self) -> bool:
        return self.client.collection_exists(self.collection_name)

    def _ensure(self, dim: int) -> None:
        if self._exists():
            return
        from qdrant_client import models

        self.client.create_collection(self.collection_name,
                                      vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE))
        for field in _INDEXED_FIELDS:
            self.client.create_payload_index(self.collection_name, field_name=field,
                                             field_schema=models.PayloadSchemaType.KEYWORD)

    @staticmethod
    def _split(payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        meta = {k: v for k, v in payload.items() if k not in _RESERVED}
        return payload["cid"], payload.get("ct", ""), meta

    # ------------------------------------------------------------------ VectorStore
    def upsert(self, ids: Sequence[str], embeddings: Sequence[Sequence[float]], payloads: Sequence[str],
               metadatas: Sequence[dict[str, Any]]) -> None:
        if not ids:
            return
        from qdrant_client import models

        self._ensure(len(embeddings[0]))
        for start in range(0, len(ids), _UPSERT_BATCH):
            points = [
                models.PointStruct(id=point_id(cid), vector=[float(x) for x in vec],
                                   payload={**meta, "cid": cid, "ct": ct})
                for cid, vec, ct, meta in zip(ids[start:start + _UPSERT_BATCH], embeddings[start:start + _UPSERT_BATCH],
                                              payloads[start:start + _UPSERT_BATCH], metadatas[start:start + _UPSERT_BATCH])
            ]
            self.client.upsert(self.collection_name, points=points, wait=True)

    def update_payloads(self, ids: Sequence[str], payloads: Sequence[str],
                        metadatas: Sequence[dict[str, Any]]) -> None:
        if not ids or not self._exists():
            return
        for cid, ct, meta in zip(ids, payloads, metadatas):
            self.client.set_payload(self.collection_name, payload={**meta, "cid": cid, "ct": ct},
                                    points=[point_id(cid)], wait=True)

    def delete(self, ids: Sequence[str]) -> None:
        if not ids or not self._exists():
            return
        from qdrant_client import models

        for start in range(0, len(ids), 1000):
            self.client.delete(self.collection_name, wait=True,
                               points_selector=models.PointIdsList(points=[point_id(c) for c in ids[start:start + 1000]]))

    def query(self, embedding: Sequence[float], n: int, where: dict[str, Any] | None = None) -> list[tuple[str, float]]:
        if n <= 0 or not self._exists():
            return []
        res = self.client.query_points(self.collection_name, query=list(map(float, embedding)), limit=n,
                                       query_filter=translate_filter(where), with_payload=["cid"])
        return [(p.payload["cid"], float(p.score)) for p in res.points]

    def get(self, ids: Sequence[str]) -> list[tuple[str, str, dict[str, Any]]]:
        if not ids or not self._exists():
            return []
        points = self.client.retrieve(self.collection_name, ids=[point_id(c) for c in ids], with_payload=True)
        found = {p.payload["cid"]: self._split(p.payload) for p in points}
        return [found[c] for c in ids if c in found]

    def get_metadata(self, ids: Sequence[str]) -> list[tuple[str, dict[str, Any]]]:
        if not ids or not self._exists():
            return []
        from qdrant_client import models

        points = self.client.retrieve(self.collection_name, ids=[point_id(c) for c in ids],
                                      with_payload=models.PayloadSelectorExclude(exclude=["ct"]))
        found = {p.payload["cid"]: {k: v for k, v in p.payload.items() if k not in _RESERVED} for p in points}
        return [(c, found[c]) for c in ids if c in found]

    def count(self) -> int:
        if not self._exists():
            return 0
        return int(self.client.count(self.collection_name, exact=True).count)

    def sample_ids(self, n: int) -> list[str]:
        if not self._exists():
            return []
        points, _ = self.client.scroll(self.collection_name, limit=n, with_payload=["cid"])
        return [p.payload["cid"] for p in points]

    def iter_all(self, batch_size: int = 1000) -> Iterator[list[tuple[str, str, dict[str, Any]]]]:
        if not self._exists():
            return
        offset = None
        while True:
            points, offset = self.client.scroll(self.collection_name, limit=batch_size, offset=offset,
                                                with_payload=True)
            if points:
                yield [self._split(p.payload) for p in points]
            if offset is None:
                return

    def get_info(self) -> dict[str, Any]:
        if not self.client.collection_exists(self.meta_collection):
            return {}
        points = self.client.retrieve(self.meta_collection, ids=[1], with_payload=True)
        return dict(points[0].payload) if points else {}

    def set_info(self, info: dict[str, Any]) -> None:
        from qdrant_client import models

        if not self.client.collection_exists(self.meta_collection):
            self.client.create_collection(self.meta_collection,
                                          vectors_config=models.VectorParams(size=1, distance=models.Distance.DOT))
        merged = {**self.get_info(), **info}
        self.client.upsert(self.meta_collection, points=[models.PointStruct(id=1, vector=[0.0], payload=merged)],
                           wait=True)

    def reset(self) -> None:
        for name in (self.collection_name, self.meta_collection):
            if self.client.collection_exists(name):
                self.client.delete_collection(name)

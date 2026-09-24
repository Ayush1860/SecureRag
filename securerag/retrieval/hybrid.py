from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from securerag.retrieval.sparse import SparseRetriever, partitions_for_filter
from securerag.retrieval.vector_store import VectorStore
from securerag.security.rbac import metadata_matches_filter as matches_filter


@dataclass
class Chunk:
    """A retrieved chunk reference. Carries no plaintext and no ciphertext: payloads are
    fetched by ID and decrypted only for the final, authorized top-k of a single request."""

    id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


def compute_rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """
    Computes standard Reciprocal Rank Fusion (RRF) scores across multiple ranked lists:
    RRF(d) = sum(1.0 / (k + rank)) for each rank list.
    """
    fused_scores: dict[str, float] = defaultdict(float)
    for ranked_ids in rankings:
        for rank, cid in enumerate(ranked_ids, start=1):
            fused_scores[cid] += 1.0 / (k + rank)
    return fused_scores


class HybridRetriever:
    """
    Hybrid retriever: dense vector search (RBAC ``where`` pre-filter in the vector store) and
    partitioned sparse BM25 (only the partitions the filter allows are opened), fused with
    Reciprocal Rank Fusion, then a strict post-fusion allow-list check on each candidate's
    metadata. Returns chunk IDs + metadata only.
    """

    def __init__(
        self,
        store: VectorStore,
        encoder: Any,
        sparse: SparseRetriever | None = None,
        fusion_k: int = 60,
        dense_candidates: int = 50,
        sparse_candidates: int = 50,
        query_prefix: str = "",
    ):
        self.query_prefix = query_prefix
        self.store = store
        self.encoder = encoder
        self.sparse = sparse
        self.fusion_k = fusion_k
        self.dense_candidates = dense_candidates
        self.sparse_candidates = sparse_candidates
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="retrieve")

    def _dense(self, query: str, where: dict[str, Any] | None) -> list[str]:
        embedding = self.encoder.encode([self.query_prefix + query], normalize_embeddings=True)[0]
        embedding = embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)
        return [cid for cid, _ in self.store.query(embedding, self.dense_candidates, where)]

    def _sparse(self, query: str, partitions: list[str]) -> list[str]:
        if self.sparse is None or not partitions:
            return []
        return [cid for cid, _ in self.sparse.search(query, partitions, self.sparse_candidates)]

    def candidates(self, query: str, where: dict[str, Any] | None = None) -> list[tuple[str, float]]:
        """Fused (chunk_id, rrf_score) candidates, best first, before the allow-list check."""
        clean_query = query.strip()
        if not clean_query:
            return []
        partitions = partitions_for_filter(where)
        if where and not partitions:
            return []  # filter not understood: fail closed

        dense_future = self._pool.submit(self._dense, clean_query, where)
        sparse_future = self._pool.submit(self._sparse, clean_query, partitions)
        fused = compute_rrf([dense_future.result(), sparse_future.result()], k=self.fusion_k)
        return sorted(fused.items(), key=lambda item: item[1], reverse=True)

    def retrieve(self, query: str, top_k: int = 5, where: dict[str, Any] | None = None,
                 limit: int | None = None) -> list[Chunk]:
        """Top ``limit or top_k`` authorized chunks (IDs + metadata)."""
        want = limit or top_k
        ranked = self.candidates(query, where)
        results: list[Chunk] = []
        # Fetch metadata in windows so a few filtered-out candidates don't shrink the result.
        for start in range(0, len(ranked), max(want * 2, 10)):
            window = ranked[start:start + max(want * 2, 10)]
            scores = dict(window)
            for cid, meta in self.store.get_metadata([cid for cid, _ in window]):
                if matches_filter(meta, where):
                    results.append(Chunk(id=cid, metadata=meta, score=scores[cid]))
                    if len(results) >= want:
                        return results
        return results

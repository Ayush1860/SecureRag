from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from rank_bm25 import BM25Okapi


@dataclass
class Chunk:
    id: str
    encrypted_text: str
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


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
    Hybrid retriever combining ChromaDB dense vector search and BM25Okapi sparse lexical search,
    fused using Reciprocal Rank Fusion (RRF).
    
    Ensures that both dense and sparse retrieval strictly enforce RBAC metadata filtering
    to prevent unauthorized lexical or semantic leakage.
    """

    def __init__(self, collection, encoder, chunks: list[Chunk], fusion_k: int = 60):
        self.collection = collection
        self.encoder = encoder
        self.chunks = chunks
        self.fusion_k = fusion_k
        self._bm25 = (
            BM25Okapi([c.text.lower().split() for c in chunks]) if chunks else None
        )
        self._by_id = {c.id: c for c in chunks}

    def _filter_allowed_chunks(self, where: dict[str, Any] | None) -> list[Chunk]:
        """Robustly extracts allowed departments and clearances from the query filter."""
        if not where:
            return self.chunks

        allowed_depts: set[str] = set()
        allowed_clearances: set[str] = set()

        conditions = where.get("$and", [where])
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            dept_cond = cond.get("department")
            if isinstance(dept_cond, dict) and "$in" in dept_cond:
                allowed_depts.update(dept_cond["$in"])
            elif isinstance(dept_cond, str):
                allowed_depts.add(dept_cond)

            clearance_cond = cond.get("clearance")
            if isinstance(clearance_cond, dict) and "$in" in clearance_cond:
                allowed_clearances.update(clearance_cond["$in"])
            elif isinstance(clearance_cond, str):
                allowed_clearances.add(clearance_cond)

        filtered = []
        for c in self.chunks:
            meta = c.metadata or {}
            dept = meta.get("department")
            clearance = meta.get("clearance")
            if allowed_depts and dept not in allowed_depts:
                continue
            if allowed_clearances and clearance not in allowed_clearances:
                continue
            filtered.append(c)

        return filtered

    def retrieve(
        self, query: str, top_k: int = 5, where: dict[str, Any] | None = None
    ) -> list[Chunk]:
        """
        Executes hybrid retrieval:
        1. Dense retrieval over ChromaDB with metadata filter.
        2. Sparse retrieval over authorized BM25 corpus matching metadata filter.
        3. Reciprocal Rank Fusion combining both candidate lists.
        4. Strict post-fusion RBAC candidate containment check.
        """
        if not self.chunks or self.collection.count() == 0:
            return []

        clean_query = query.strip()
        if not clean_query:
            return []

        # 1. Dense retrieval
        q_emb = self.encoder.encode([clean_query], normalize_embeddings=True)[0].tolist()
        num_candidates = min(max(top_k * 2, top_k), self.collection.count())

        dense_kwargs: dict[str, Any] = {
            "query_embeddings": [q_emb],
            "n_results": num_candidates,
        }
        if where:
            dense_kwargs["where"] = where

        dense_result = self.collection.query(**dense_kwargs)
        dense_ids = dense_result.get("ids", [[]])[0]

        # 2. Sparse retrieval (strictly filtered by RBAC metadata)
        allowed_chunks = self._filter_allowed_chunks(where)
        allowed_id_set = {c.id for c in allowed_chunks}

        sparse_ids: list[str] = []
        if self._bm25 and allowed_chunks:
            tokens = clean_query.lower().split()
            if tokens:
                corpus_scores = self._bm25.get_scores(tokens)
                ranked_sparse = sorted(
                    (
                        (c.id, corpus_scores[i])
                        for i, c in enumerate(self.chunks)
                        if c.id in allowed_id_set and corpus_scores[i] > 0
                    ),
                    key=lambda x: x[1],
                    reverse=True,
                )
                sparse_ids = [cid for cid, _ in ranked_sparse[: top_k * 2]]

        # 3. Reciprocal Rank Fusion
        fused = compute_rrf([dense_ids, sparse_ids], k=self.fusion_k)

        # 4. Return top_k Chunk instances strictly restricted to allowed chunks
        ranked_cids = [
            cid for cid in sorted(fused.keys(), key=lambda cid: fused[cid], reverse=True)
            if cid in self._by_id and cid in allowed_id_set
        ][:top_k]

        return [self._by_id[cid] for cid in ranked_cids]

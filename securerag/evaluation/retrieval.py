"""
Retrieval Evaluation Module for SecureRAG
Computes Recall@K, Precision@K, and Mean Reciprocal Rank (MRR)
over hybrid dense + sparse retrieval with RBAC enforcement.
"""

from typing import Any, Sequence
from securerag.evaluation.datasets import RETRIEVAL_BENCHMARK_CASES, RetrievalCase
from securerag.retrieval.hybrid import HybridRetriever
from securerag.security.rbac import build_chroma_filter


def evaluate_retrieval(
    retriever: HybridRetriever,
    test_cases: Sequence[RetrievalCase] | None = None,
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict[str, Any]:
    """
    Evaluates hybrid retrieval quality across benchmark queries:
    - Recall@K: Proportion of target relevant documents present in top-K results.
    - Precision@K: Proportion of retrieved documents in top-K that are relevant.
    - MRR (Mean Reciprocal Rank): Average reciprocal rank of the first relevant document.
    """
    if test_cases is None:
        test_cases = RETRIEVAL_BENCHMARK_CASES

    max_k = max(ks) if ks else 5
    total_queries = len(test_cases)
    if total_queries == 0:
        return {
            "total_queries": 0,
            "recall_at_k": {k: 0.0 for k in ks},
            "precision_at_k": {k: 0.0 for k in ks},
            "mrr": 0.0,
            "detailed_results": [],
        }

    per_query_recall: dict[int, list[float]] = {k: [] for k in ks}
    per_query_precision: dict[int, list[float]] = {k: [] for k in ks}
    reciprocal_ranks: list[float] = []
    detailed_results: list[dict[str, Any]] = []

    for case in test_cases:
        query = case["query"]
        role = case["role"]
        targets = set(case.get("target_sources") or [case["target_source"]])
        role_filter = build_chroma_filter(role)

        # Execute hybrid retrieval respecting RBAC filter
        retrieved_chunks = retriever.retrieve(query, top_k=max_k, where=role_filter)
        retrieved_sources = [c.metadata.get("source", "") for c in retrieved_chunks]

        # Calculate Reciprocal Rank (first relevant document rank)
        rr = 0.0
        for rank, src in enumerate(retrieved_sources, start=1):
            if src in targets:
                rr = 1.0 / rank
                break
        reciprocal_ranks.append(rr)

        query_hits_at_k: dict[int, int] = {}
        for k in ks:
            top_k_sources = retrieved_sources[:k]
            hits = sum(1 for src in top_k_sources if src in targets)
            query_hits_at_k[k] = hits
            
            # Recall@K = relevant documents retrieved in top K / total relevant documents
            rec = hits / len(targets) if targets else 0.0
            per_query_recall[k].append(rec)

            # Precision@K = relevant documents retrieved in top K / K
            prec = hits / k if k > 0 else 0.0
            per_query_precision[k].append(prec)

        detailed_results.append({
            "query": query,
            "role": role,
            "targets": list(targets),
            "retrieved_sources": retrieved_sources,
            "reciprocal_rank": rr,
            "recall_at_k": {k: per_query_recall[k][-1] for k in ks},
            "precision_at_k": {k: per_query_precision[k][-1] for k in ks},
        })

    recall_at_k = {k: float(sum(per_query_recall[k]) / total_queries) for k in ks}
    precision_at_k = {k: float(sum(per_query_precision[k]) / total_queries) for k in ks}
    mrr = float(sum(reciprocal_ranks) / total_queries)

    return {
        "total_queries": total_queries,
        "recall_at_k": recall_at_k,
        "precision_at_k": precision_at_k,
        "mrr": mrr,
        "detailed_results": detailed_results,
    }

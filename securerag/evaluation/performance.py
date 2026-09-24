"""
Performance & Latency Evaluation Module for SecureRAG
Provides isolated and end-to-end latency benchmarks for:
1. Embedding latency (SentenceTransformer encoding)
2. Retrieval latency (Dense + BM25 + RRF fusion)
3. Generation latency (Grounded LLM synthesis)
4. End-to-End latency (Full LangGraph pipeline)
"""

import time
from collections.abc import Sequence
from typing import Any

import numpy as np

from securerag.evaluation.datasets import RETRIEVAL_BENCHMARK_CASES, RetrievalCase
from securerag.llm.providers import SYSTEM_PROMPT, call_llm
from securerag.pipeline.graph import SecureRAG
from securerag.retrieval.hybrid import HybridRetriever
from securerag.security.rbac import build_chroma_filter


def _compute_distribution(latencies_ms: list[float]) -> dict[str, float]:
    """Computes standard latency summary percentiles and metrics in milliseconds."""
    arr = np.array(latencies_ms)
    return {
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "mean": float(np.mean(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "std": float(np.std(arr)),
    }


# ---------------------------------------------------------------------------
# 1. Isolated Embedding Latency
# ---------------------------------------------------------------------------
def evaluate_embedding_latency(
    encoder: Any,
    queries: Sequence[str] | None = None,
    iterations: int = 20,
    warmup: int = 3,
) -> dict[str, Any]:
    """
    Measures isolated query embedding generation latency using the configured encoder.
    """
    if queries is None:
        queries = [c["query"] for c in RETRIEVAL_BENCHMARK_CASES]

    # Warmup
    for q in queries[:warmup]:
        encoder.encode([q], normalize_embeddings=True)

    latencies: list[float] = []
    num_queries = len(queries)
    for i in range(iterations):
        q = queries[i % num_queries]
        t0 = time.perf_counter()
        encoder.encode([q], normalize_embeddings=True)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)

    stats = _compute_distribution(latencies)
    return {
        "iterations": iterations,
        "metrics_ms": stats,
        "raw_ms": latencies,
    }


# ---------------------------------------------------------------------------
# 2. Isolated Retrieval Latency
# ---------------------------------------------------------------------------
def evaluate_retrieval_latency(
    retriever: HybridRetriever,
    test_cases: Sequence[RetrievalCase] | None = None,
    top_k: int = 5,
    iterations: int = 20,
    warmup: int = 3,
) -> dict[str, Any]:
    """
    Measures isolated hybrid retrieval latency (Dense ChromaDB search + BM25 + RRF fusion).
    """
    if test_cases is None:
        test_cases = RETRIEVAL_BENCHMARK_CASES

    # Warmup
    for c in test_cases[:warmup]:
        where = build_chroma_filter(c["role"])
        retriever.retrieve(c["query"], top_k=top_k, where=where)

    latencies: list[float] = []
    num_cases = len(test_cases)
    for i in range(iterations):
        case = test_cases[i % num_cases]
        where = build_chroma_filter(case["role"])
        t0 = time.perf_counter()
        retriever.retrieve(case["query"], top_k=top_k, where=where)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)

    stats = _compute_distribution(latencies)
    return {
        "iterations": iterations,
        "metrics_ms": stats,
        "raw_ms": latencies,
    }


# ---------------------------------------------------------------------------
# 3. Isolated Generation Latency
# ---------------------------------------------------------------------------
def evaluate_generation_latency(
    test_cases: Sequence[RetrievalCase] | None = None,
    iterations: int = 20,
    warmup: int = 3,
) -> dict[str, Any]:
    """
    Measures isolated LLM generation latency on realistic grounded context prompts.
    """
    sample_context = (
        "The Sentinel AMR navigation stack runs a three-layer architecture: a perception layer, "
        "a planning layer, and a control layer running on a real-time Linux kernel with a 50ms budget. "
        "Q3 gross margin was 34.2%. Cash runway is 14 months."
    )
    prompts = [
        f"{sample_context}\n\nQuestion: {c['query']}"
        for c in (test_cases or RETRIEVAL_BENCHMARK_CASES)
    ]

    # Warmup
    for p in prompts[:warmup]:
        call_llm(SYSTEM_PROMPT, p)

    latencies: list[float] = []
    num_prompts = len(prompts)
    for i in range(iterations):
        prompt = prompts[i % num_prompts]
        t0 = time.perf_counter()
        call_llm(SYSTEM_PROMPT, prompt)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)

    stats = _compute_distribution(latencies)
    return {
        "iterations": iterations,
        "metrics_ms": stats,
        "raw_ms": latencies,
    }


# ---------------------------------------------------------------------------
# 4. End-to-End Pipeline Latency
# ---------------------------------------------------------------------------
def evaluate_end_to_end_latency(
    engine: SecureRAG,
    test_cases: Sequence[RetrievalCase] | None = None,
    top_k: int = 5,
    iterations: int = 20,
    warmup: int = 3,
) -> dict[str, Any]:
    """
    Measures complete end-to-end pipeline latency through LangGraph orchestration
    (validate -> retrieve -> authorize -> decrypt_sanitize -> generate -> audit).
    """
    if test_cases is None:
        test_cases = RETRIEVAL_BENCHMARK_CASES

    # Warmup
    for c in test_cases[:warmup]:
        engine.query(c["query"], c["role"], top_k=top_k)

    latencies: list[float] = []
    num_cases = len(test_cases)
    for i in range(iterations):
        case = test_cases[i % num_cases]
        t0 = time.perf_counter()
        res = engine.query(case["query"], case["role"], top_k=top_k)
        t1 = time.perf_counter()
        # Prefer internal timer if recorded, otherwise wall clock
        pipeline_ms = res.get("latency_ms", (t1 - t0) * 1000.0)
        latencies.append(pipeline_ms)

    stats = _compute_distribution(latencies)
    return {
        "iterations": iterations,
        "metrics_ms": stats,
        "raw_ms": latencies,
    }


# ---------------------------------------------------------------------------
# 5. Unified Performance Benchmark
# ---------------------------------------------------------------------------
def evaluate_performance(
    encoder: Any,
    retriever: HybridRetriever,
    engine: SecureRAG,
    test_cases: Sequence[RetrievalCase] | None = None,
    iterations: int = 20,
) -> dict[str, Any]:
    """
    Executes the entire performance latency suite across all stages.
    """
    emb_results = evaluate_embedding_latency(encoder, iterations=iterations)
    ret_results = evaluate_retrieval_latency(retriever, test_cases, iterations=iterations)
    gen_results = evaluate_generation_latency(test_cases, iterations=iterations)
    e2e_results = evaluate_end_to_end_latency(engine, test_cases, iterations=iterations)

    return {
        "iterations_per_stage": iterations,
        "embedding_latency": emb_results["metrics_ms"],
        "retrieval_latency": ret_results["metrics_ms"],
        "generation_latency": gen_results["metrics_ms"],
        "end_to_end_latency": e2e_results["metrics_ms"],
        "raw_samples": {
            "embedding": emb_results["raw_ms"],
            "retrieval": ret_results["raw_ms"],
            "generation": gen_results["raw_ms"],
            "end_to_end": e2e_results["raw_ms"],
        },
    }

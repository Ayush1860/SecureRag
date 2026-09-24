import pytest

from scripts.evaluate import (
    evaluate_prompt_injection as compat_evaluate_prompt_injection,
)
from scripts.evaluate import (
    evaluate_rbac_leak_rate as compat_evaluate_rbac_leak_rate,
)
from securerag.config import get_settings
from securerag.evaluation.datasets import (
    INJECTION_BENCHMARK_CASES,
    RETRIEVAL_BENCHMARK_CASES,
)
from securerag.evaluation.performance import (
    evaluate_performance,
)
from securerag.evaluation.retrieval import evaluate_retrieval
from securerag.evaluation.runner import generate_markdown_report
from securerag.evaluation.security import (
    evaluate_cross_boundary_retrieval_leak_rate,
    evaluate_prompt_injection,
    evaluate_rbac_policy_leak_rate,
    evaluate_security,
)
from securerag.pipeline.graph import SecureRAG
from securerag.retrieval.store import open_serving_stack, run_ingestion
from securerag.security.encryption import VectorStoreEncryptor


@pytest.fixture(scope="module")
def rag_components():
    # Real embedding model over data/sample, built into the configured store (full rebuild).
    settings = get_settings()
    encryptor = VectorStoreEncryptor()
    run_ingestion(settings, encryptor, full_rebuild=True)
    stack = open_serving_stack(settings, encryptor)
    engine = SecureRAG(stack.store, stack.retriever, encryptor, settings.audit_log_path)
    return {
        "settings": settings,
        "encryptor": encryptor,
        "store": stack.store,
        "encoder": stack.encoder,
        "retriever": stack.retriever,
        "engine": engine,
    }


def test_prompt_injection_evaluation_metrics():
    stats = evaluate_prompt_injection()
    assert stats["true_positives"] > 0
    assert stats["true_negatives"] > 0
    assert stats["prompt_injection_detection_rate"] >= 0.90
    assert stats["false_positive_rate"] <= 0.10
    assert stats["f1_score"] >= 0.90
    assert stats["total_samples"] == len(INJECTION_BENCHMARK_CASES)


def test_compat_prompt_injection_metrics():
    stats = compat_evaluate_prompt_injection()
    assert stats["tp"] > 0
    assert stats["tn"] > 0
    assert stats["recall"] >= 0.90
    assert stats["fpr"] <= 0.10
    assert stats["f1"] >= 0.90


def test_rbac_zero_leak_rate():
    stats = evaluate_rbac_policy_leak_rate()
    assert stats["total_checks"] > 0
    assert stats["unauthorized_leaks"] == 0
    assert stats["leak_rate"] == 0.0


def test_compat_rbac_zero_leak_rate():
    stats = compat_evaluate_rbac_leak_rate()
    assert stats["total_checks"] > 0
    assert stats["unauthorized_leaks"] == 0
    assert stats["leak_rate"] == 0.0


def test_cross_boundary_retrieval_zero_leaks(rag_components):
    engine = rag_components["engine"]
    stats = evaluate_cross_boundary_retrieval_leak_rate(engine)
    assert stats["total_cross_boundary_queries"] > 0
    assert stats["unauthorized_retrieval_leaks"] == 0
    assert stats["unauthorized_retrieval_rate"] == 0.0


def test_unified_security_evaluation(rag_components):
    engine = rag_components["engine"]
    stats = evaluate_security(engine)
    assert stats["unauthorized_retrieval_rate"] == 0.0
    assert stats["prompt_injection_detection_rate"] >= 0.90
    assert stats["false_positive_rate"] <= 0.10


def test_retrieval_metrics(rag_components):
    retriever = rag_components["retriever"]
    stats = evaluate_retrieval(retriever, RETRIEVAL_BENCHMARK_CASES, ks=(1, 3, 5))

    assert stats["total_queries"] == len(RETRIEVAL_BENCHMARK_CASES)
    # Recall@K monotonically non-decreasing
    assert stats["recall_at_k"][1] <= stats["recall_at_k"][3] <= stats["recall_at_k"][5]
    assert stats["recall_at_k"][1] >= 0.80
    assert stats["recall_at_k"][5] >= 0.95
    # Precision@K exists and valid
    assert 0.0 <= stats["precision_at_k"][1] <= 1.0
    assert 0.0 <= stats["precision_at_k"][3] <= 1.0
    assert 0.0 <= stats["precision_at_k"][5] <= 1.0
    # MRR valid
    assert stats["mrr"] >= 0.85


def test_performance_latencies(rag_components):
    encoder = rag_components["encoder"]
    retriever = rag_components["retriever"]
    engine = rag_components["engine"]

    perf = evaluate_performance(
        encoder=encoder,
        retriever=retriever,
        engine=engine,
        test_cases=RETRIEVAL_BENCHMARK_CASES[:3],
        iterations=3,
    )

    for stage in ("embedding_latency", "retrieval_latency", "generation_latency", "end_to_end_latency"):
        assert stage in perf
        metrics = perf[stage]
        for key in ("p50", "p95", "p99", "mean", "min", "max"):
            assert key in metrics
            assert metrics[key] >= 0.0
        assert metrics["min"] <= metrics["p50"] <= metrics["max"]


def test_markdown_report_generation():
    dummy_payload = {
        "metadata": {
            "timestamp": "2026-09-02T00:00:00Z",
            "python_version": "3.11.4",
            "platform": "Windows",
            "llm_provider": "mock",
            "embedding_model": "all-MiniLM-L6-v2",
            "fusion_k": 60,
        },
        "retrieval": {
            "total_queries": 5,
            "recall_at_k": {"1": 1.0, "3": 1.0, "5": 1.0},
            "precision_at_k": {"1": 1.0, "3": 0.333, "5": 0.2},
            "mrr": 1.0,
        },
        "security": {
            "unauthorized_retrieval_rate": 0.0,
            "prompt_injection_detection_rate": 1.0,
            "false_positive_rate": 0.0,
            "prompt_injection": {
                "total_samples": 40,
                "true_positives": 20,
                "false_negatives": 0,
                "false_positives": 0,
                "true_negatives": 20,
                "prompt_injection_detection_rate": 1.0,
                "false_positive_rate": 0.0,
                "precision": 1.0,
                "f1_score": 1.0,
                "accuracy": 1.0,
            },
            "rbac_policy": {
                "total_checks": 40,
                "unauthorized_leaks": 0,
                "leak_rate": 0.0,
            },
            "cross_boundary_retrieval": {
                "total_cross_boundary_queries": 6,
                "unauthorized_retrieval_leaks": 0,
                "unauthorized_retrieval_rate": 0.0,
            },
        },
        "performance": {
            "iterations_per_stage": 10,
            "embedding_latency": {"p50": 10.0, "p95": 15.0, "p99": 20.0, "mean": 11.0, "min": 9.0, "max": 21.0},
            "retrieval_latency": {"p50": 15.0, "p95": 22.0, "p99": 25.0, "mean": 16.0, "min": 14.0, "max": 26.0},
            "generation_latency": {"p50": 0.5, "p95": 1.0, "p99": 1.5, "mean": 0.6, "min": 0.4, "max": 1.6},
            "end_to_end_latency": {"p50": 28.0, "p95": 40.0, "p99": 45.0, "mean": 30.0, "min": 25.0, "max": 48.0},
        },
    }
    md = generate_markdown_report(dummy_payload)
    assert "# SecureRAG — Benchmark Evaluation Report" in md
    assert "Recall@1" in md
    assert "Precision@1" in md
    assert "Unauthorized Retrieval Rate" in md
    assert "Embedding Latency" in md

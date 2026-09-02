"""
SecureRAG Evaluation Suite
Comprehensive benchmarks for Retrieval, Security Hardening, and Pipeline Latency.
"""

from securerag.evaluation.datasets import (
    INJECTION_BENCHMARK_CASES,
    RETRIEVAL_BENCHMARK_CASES,
    RBAC_POLICY_TEST_CASES,
    CROSS_BOUNDARY_QUERY_CASES,
)
from securerag.evaluation.retrieval import evaluate_retrieval
from securerag.evaluation.security import (
    evaluate_prompt_injection,
    evaluate_rbac_policy_leak_rate,
    evaluate_cross_boundary_retrieval_leak_rate,
    evaluate_security,
)
from securerag.evaluation.performance import (
    evaluate_embedding_latency,
    evaluate_retrieval_latency,
    evaluate_generation_latency,
    evaluate_end_to_end_latency,
    evaluate_performance,
)
from securerag.evaluation.runner import run_evaluation_suite

__all__ = [
    "INJECTION_BENCHMARK_CASES",
    "RETRIEVAL_BENCHMARK_CASES",
    "RBAC_POLICY_TEST_CASES",
    "CROSS_BOUNDARY_QUERY_CASES",
    "evaluate_retrieval",
    "evaluate_prompt_injection",
    "evaluate_rbac_policy_leak_rate",
    "evaluate_cross_boundary_retrieval_leak_rate",
    "evaluate_security",
    "evaluate_embedding_latency",
    "evaluate_retrieval_latency",
    "evaluate_generation_latency",
    "evaluate_end_to_end_latency",
    "evaluate_performance",
    "run_evaluation_suite",
]

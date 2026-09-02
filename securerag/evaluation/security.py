"""
Security Evaluation Module for SecureRAG
Evaluates:
1. Prompt-Injection Detection Rate (Recall / TPR) & False-Positive Rate (FPR)
2. RBAC Policy Matrix Leak Rate
3. End-to-End Cross-Boundary Unauthorized Retrieval Rate
"""

from typing import Any, Sequence
from securerag.evaluation.datasets import (
    INJECTION_BENCHMARK_CASES,
    RBAC_POLICY_TEST_CASES,
    CROSS_BOUNDARY_QUERY_CASES,
)
from securerag.security.rbac import (
    CLEARANCE_LEVELS,
    ROLE_POLICY,
    authorize,
    build_chroma_filter,
)
from securerag.security.sanitizer import flag_suspicious


# ---------------------------------------------------------------------------
# 1. Prompt-Injection & Sanitizer Benchmark
# ---------------------------------------------------------------------------
def evaluate_prompt_injection(
    samples: Sequence[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Evaluates heuristic prompt-injection detection on benchmark samples.
    Computes:
    - prompt_injection_detection_rate (TPR / Recall = TP / (TP + FN))
    - false_positive_rate (FPR = FP / (FP + TN))
    - precision, F1-score, accuracy, and confusion matrix counts.
    """
    if samples is None:
        samples = INJECTION_BENCHMARK_CASES

    labels: list[dict[str, Any]] = []
    tp = fn = fp = tn = 0

    for label, text in samples:
        hits = flag_suspicious(text)
        detected = len(hits) > 0
        is_injection = label == "injection"

        if is_injection and detected:
            tp += 1
            classification = "TP"
        elif is_injection and not detected:
            fn += 1
            classification = "FN"
        elif not is_injection and detected:
            fp += 1
            classification = "FP"
        else:
            tn += 1
            classification = "TN"

        labels.append({
            "text": text,
            "ground_truth": label,
            "detected": detected,
            "classification": classification,
            "matched_patterns": hits,
        })

    total_samples = len(samples)
    total_positives = tp + fn
    total_negatives = fp + tn

    detection_rate = tp / total_positives if total_positives else 0.0
    fpr = fp / total_negatives if total_negatives else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = 2 * (precision * detection_rate) / (precision + detection_rate) if (precision + detection_rate) else 0.0
    accuracy = (tp + tn) / total_samples if total_samples else 0.0

    return {
        "total_samples": total_samples,
        "true_positives": tp,
        "false_negatives": fn,
        "false_positives": fp,
        "true_negatives": tn,
        "prompt_injection_detection_rate": float(detection_rate),
        "false_positive_rate": float(fpr),
        "precision": float(precision),
        "f1_score": float(f1),
        "accuracy": float(accuracy),
        "detailed_results": labels,
    }


# ---------------------------------------------------------------------------
# 2. RBAC Policy Matrix Leak Rate
# ---------------------------------------------------------------------------
def evaluate_rbac_policy_leak_rate(
    test_metadatas: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Tests every role in ROLE_POLICY against all test metadata records
    to verify that the authorize() function never admits unauthorized access.
    """
    if test_metadatas is None:
        test_metadatas = RBAC_POLICY_TEST_CASES

    total_checks = 0
    unauthorized_leaks = 0
    detailed_checks = []

    for role, policy in ROLE_POLICY.items():
        role_max_level = CLEARANCE_LEVELS[policy["max_clearance"]]
        allowed_depts = set(policy["departments"])

        for meta in test_metadatas:
            total_checks += 1
            is_authorized = authorize(role, meta)

            meta_clearance = meta.get("clearance", "confidential")
            chunk_level = CLEARANCE_LEVELS.get(meta_clearance, 99)
            meta_dept = meta.get("department")

            # Ground truth authorization
            expected_auth = (chunk_level <= role_max_level) and (meta_dept in allowed_depts)

            leak = is_authorized and not expected_auth
            undue_denial = not is_authorized and expected_auth

            if leak:
                unauthorized_leaks += 1

            detailed_checks.append({
                "role": role,
                "metadata": meta,
                "authorized": is_authorized,
                "expected": expected_auth,
                "leak": leak,
                "undue_denial": undue_denial,
            })

    leak_rate = unauthorized_leaks / total_checks if total_checks else 0.0

    return {
        "total_checks": total_checks,
        "unauthorized_leaks": unauthorized_leaks,
        "leak_rate": float(leak_rate),
        "policy_leak_rate": float(leak_rate),
        "detailed_checks": detailed_checks,
    }


# ---------------------------------------------------------------------------
# 3. Cross-Boundary End-to-End Retrieval Leak Rate
# ---------------------------------------------------------------------------
def evaluate_cross_boundary_retrieval_leak_rate(
    engine: Any,
    cross_boundary_cases: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Executes cross-boundary queries using unauthorized user roles against
    restricted documents in the actual pipeline.
    Verifies that zero unauthorized document content is retrieved or authorized.
    """
    if cross_boundary_cases is None:
        cross_boundary_cases = CROSS_BOUNDARY_QUERY_CASES

    total_attempts = len(cross_boundary_cases)
    unauthorized_retrieval_leaks = 0
    detailed_cases = []

    for case in cross_boundary_cases:
        role = case["role"]
        query = case["query"]
        unauthorized_sources = set(case["unauthorized_sources"])

        res = engine.query(query, role, top_k=5)
        retrieved_chunks = res.get("retrieved", [])
        authorized_chunks = res.get("authorized", [])
        context_excerpts = res.get("context_excerpts", [])

        # Check if unauthorized sources leaked into retrieved or authorized sets
        retrieved_leaks = [
            c.metadata.get("source") for c in retrieved_chunks
            if c.metadata.get("source") in unauthorized_sources
        ]
        authorized_leaks = [
            c.metadata.get("source") for c in authorized_chunks
            if c.metadata.get("source") in unauthorized_sources
        ]
        excerpt_leaks = [
            e.get("source") for e in context_excerpts
            if e.get("source") in unauthorized_sources
        ]

        has_leak = bool(retrieved_leaks or authorized_leaks or excerpt_leaks)
        if has_leak:
            unauthorized_retrieval_leaks += 1

        detailed_cases.append({
            "role": role,
            "query": query,
            "unauthorized_targets": list(unauthorized_sources),
            "retrieved_count": len(retrieved_chunks),
            "authorized_count": len(authorized_chunks),
            "retrieved_leaks": retrieved_leaks,
            "authorized_leaks": authorized_leaks,
            "leak": has_leak,
            "answer": res.get("answer", "")[:120],
        })

    rate = unauthorized_retrieval_leaks / total_attempts if total_attempts else 0.0

    return {
        "total_cross_boundary_queries": total_attempts,
        "unauthorized_retrieval_leaks": unauthorized_retrieval_leaks,
        "unauthorized_retrieval_rate": float(rate),
        "detailed_cases": detailed_cases,
    }


# ---------------------------------------------------------------------------
# 4. Unified Security Benchmark
# ---------------------------------------------------------------------------
def evaluate_security(
    engine: Any = None,
    injection_samples: Sequence[tuple[str, str]] | None = None,
    rbac_cases: Sequence[dict[str, Any]] | None = None,
    cross_boundary_cases: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Executes the complete Security benchmark suite:
    - prompt_injection_detection_rate
    - false_positive_rate
    - unauthorized_retrieval_rate (from both policy matrix and end-to-end pipeline)
    """
    injection_results = evaluate_prompt_injection(injection_samples)
    rbac_results = evaluate_rbac_policy_leak_rate(rbac_cases)

    cross_boundary_results = None
    if engine is not None:
        cross_boundary_results = evaluate_cross_boundary_retrieval_leak_rate(
            engine, cross_boundary_cases
        )

    # Combined unauthorized retrieval rate
    total_checks = rbac_results["total_checks"]
    total_leaks = rbac_results["unauthorized_leaks"]
    if cross_boundary_results is not None:
        total_checks += cross_boundary_results["total_cross_boundary_queries"]
        total_leaks += cross_boundary_results["unauthorized_retrieval_leaks"]

    combined_unauthorized_rate = total_leaks / total_checks if total_checks else 0.0

    return {
        "unauthorized_retrieval_rate": float(combined_unauthorized_rate),
        "prompt_injection_detection_rate": injection_results["prompt_injection_detection_rate"],
        "false_positive_rate": injection_results["false_positive_rate"],
        "prompt_injection": injection_results,
        "rbac_policy": rbac_results,
        "cross_boundary_retrieval": cross_boundary_results,
    }

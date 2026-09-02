"""
CLI Entrypoint for the SecureRAG Evaluation & Verification Suite.
Executes the evaluation runner, computes real benchmark metrics across
Retrieval, Security, and Latency, and saves reports to reports/evaluation.json and reports/evaluation.md.
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from securerag.evaluation.runner import run_evaluation_suite
from securerag.evaluation.security import (
    evaluate_prompt_injection as _eval_inj,
    evaluate_rbac_policy_leak_rate as _eval_rbac,
)
from securerag.evaluation.retrieval import evaluate_retrieval
from securerag.evaluation.performance import evaluate_performance


def evaluate_prompt_injection(samples=None):
    """Backwards-compatible wrapper for prompt injection tests."""
    res = _eval_inj(samples)
    return {
        "tp": res["true_positives"],
        "fn": res["false_negatives"],
        "fp": res["false_positives"],
        "tn": res["true_negatives"],
        "precision": res["precision"],
        "recall": res["prompt_injection_detection_rate"],
        "f1": res["f1_score"],
        "fpr": res["false_positive_rate"],
        "prompt_injection_detection_rate": res["prompt_injection_detection_rate"],
        "false_positive_rate": res["false_positive_rate"],
    }


def evaluate_rbac_leak_rate(test_metadatas=None):
    """Backwards-compatible wrapper for RBAC leak rate tests."""
    res = _eval_rbac(test_metadatas)
    return {
        "total_checks": res["total_checks"],
        "unauthorized_leaks": res["unauthorized_leaks"],
        "leak_rate": res["leak_rate"],
    }


def run_all_evaluations():
    """Main CLI runner invoking the full evaluation suite."""
    return run_evaluation_suite(
        output_dir="reports",
        json_filename="evaluation.json",
        md_filename="evaluation.md",
        iterations=15,
    )


if __name__ == "__main__":
    run_all_evaluations()

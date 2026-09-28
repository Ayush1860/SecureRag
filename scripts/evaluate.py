"""
Build reports/evaluation.{md,json} from the real benchmark runs:

  * retrieval quality under RBAC on BEIR (reports/beir/<dataset>.json, from scripts/eval_beir.py)
  * security and performance at scale (reports/scale/<prefix>_docs_*.json, from scripts/benchmark_scale.py)

    python scripts/evaluate.py                 # aggregate existing runs
    python scripts/evaluate.py --fixture       # also run the 5-document smoke suite (unit-test fixture)

The 5-document fixture is kept as a smoke test only; its numbers are not a benchmark.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from securerag.evaluation.security import evaluate_prompt_injection as _eval_inj  # noqa: E402
from securerag.evaluation.security import evaluate_rbac_policy_leak_rate as _eval_rbac  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODES = ["dense", "sparse", "hybrid", "hybrid_rerank"]


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


# ---------------------------------------------------------------------------------- aggregation

def _load_json(pattern: str) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ROOT.glob(pattern))]


def _role_spread(report: dict[str, Any]) -> tuple[dict[str, float], float]:
    """Per-role retrieval p95 and the slowest/fastest ratio (1.0 = filtering costs nothing)."""
    p95 = {role: stats["retrieval_ms"]["p95"] for role, stats in report["serve"]["per_role"].items()}
    return p95, max(p95.values()) / min(p95.values())


def _qdrant_section(chroma_by_docs: dict[int, dict[str, Any]]) -> list[str]:
    """Docker + Qdrant server runs from .github/workflows/docker-bench.yml (reports/scale/gha_qdrant_docs_*)."""
    runs = sorted((r for r in _load_json("reports/scale/gha_qdrant_docs_*.json") if r["ingest"]["ok"]),
                  key=lambda r: r["corpus"]["docs"])
    if not runs:
        return []
    hw = runs[-1]["hardware"]
    lines = ["## 4. Qdrant server in Docker (GitHub Actions)", "",
             f"`docker compose` on a GitHub runner ({hw['cpu_count']} CPUs, {hw['ram_gb']} GB RAM, no GPU): the API "
             "image against a Qdrant server with payload indexes, after an RBAC smoke test of the running container.",
             "The hardware differs from sections 2–3, so compare how retrieval latency changes across roles, not the",
             "absolute milliseconds. The spread is the slowest role's p95 divided by the fastest role's.", "",
             "| Docs | Chunks | Queries | Canary leaks | Escalations | Chunks/s | Retrieval p95 by role (ms) | "
             "Spread | Chroma spread (§3) |", "|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        s = r["serve"]
        p95, spread = _role_spread(r)
        ref = chroma_by_docs.get(r["corpus"]["docs"])
        ref_spread = f"{_role_spread(ref)[1]:.2f}×" if ref else "–"
        roles = ", ".join(f"{role} {v:.0f}" for role, v in p95.items())
        queries = sum(p["queries"] for p in s["per_role"].values())
        lines.append(f"| {r['corpus']['docs']:,} | {r['ingest']['chunks']:,} | {queries} | **{s['canary_leaks']}** | "
                     f"**{(s.get('role_spoofing') or {}).get('escalations', '–')}** | {r['ingest']['chunks_per_s']} | "
                     f"{roles} | {spread:.2f}× | {ref_spread} |")
    lines += ["", "With Chroma, the more restricted the role, the slower its search (a metadata filter scans the "
              "matching rows);", "Qdrant's indexed payload filter keeps every role at about the same latency.", ""]
    return lines


def build_report(scale_prefix: str = "phase6") -> dict[str, Any]:
    beir = _load_json("reports/beir/*.json")
    scale = sorted((r for r in _load_json(f"reports/scale/{scale_prefix}_docs_*.json") if r["ingest"]["ok"]),
                   key=lambda r: r["corpus"]["docs"])
    lines = [
        "# SecureRAG — Evaluation Report",
        "",
        f"Generated {time.strftime('%Y-%m-%d %H:%M')} from `reports/beir/*.json` and "
        f"`reports/scale/{scale_prefix}_docs_*.json`. Reproduce with the commands at the end.",
        "",
        "> The earlier version of this report headlined *100% recall* measured on 5 hand-written documents.",
        "> That fixture is still in the unit tests (`tests/test_evaluation.py`); it is not a benchmark.",
        "",
    ]
    if scale:
        hw = scale[-1].get("hardware", {})
        lines += [f"Hardware: {hw.get('cpu_count')} CPUs, {hw.get('ram_gb')} GB RAM, {hw.get('gpu') or 'no GPU'} "
                  f"(embeddings on GPU), {hw.get('platform')}. LLM: deterministic mock (latency excludes generation).",
                  ""]

    # --- retrieval quality
    lines += ["## 1. Retrieval quality under RBAC (BEIR)", "",
              "Documents get deterministic synthetic (department, clearance) labels and go through the real",
              "pipeline (chunking, AES-GCM, partitioned HMAC BM25). Per role, a query is scored only against",
              "the relevant documents that role may see. `exec` sees the whole corpus, so its row is",
              "comparable to published BEIR numbers.", ""]
    for b in beir:
        lines += [f"### {b['dataset']} — {b['docs']:,} docs, {b['chunks']:,} chunks, {b['test_queries']} test queries",
                  "", "| Role (evaluable queries) | Mode | nDCG@10 | Recall@10 | Recall@50 | MRR@10 | Leaks |",
                  "|---|---|---|---|---|---|---|"]
        for role, modes in b["results"].items():
            for mode in MODES:
                if mode in modes:
                    m = modes[mode]
                    lines.append(f"| {role} ({m['queries']}) | {mode} | {m['ndcg@10']:.3f} | {m['recall@10']:.3f} | "
                                 f"{m['recall@50']:.3f} | {m['mrr@10']:.3f} | {m['leaks']} |")
        lines.append("")

    # --- security at scale
    if scale:
        lines += ["## 2. Security at scale (synthetic corpus with canaries)", "",
                  "Each run sends 200 queries per role (topical, canary probes, injection-style probes).",
                  "A leak is a canary from a document the role may not read showing up in its answer or context.",
                  "", "| Docs | Chunks | Queries | Canary leaks | Injection detection (TP/FN) | Injection FPR | "
                  "Role-spoof attempts | Escalations |", "|---|---|---|---|---|---|---|---|"]
        for r in scale:
            s = r["serve"]
            inj = s.get("injection_detection") or {}
            spoof = s.get("role_spoofing") or {}
            queries = sum(p["queries"] for p in s["per_role"].values())
            det = (f"{inj.get('detection_rate', 0):.1%} ({inj.get('true_positives')}/{inj.get('false_negatives')})"
                   if inj else "–")
            fpr = f"{inj.get('false_positive_rate', 0):.4%}" if inj else "–"
            lines.append(f"| {r['corpus']['docs']:,} | {r['ingest']['chunks']:,} | {queries} | **{s['canary_leaks']}** "
                         f"| {det} | {fpr} | {spoof.get('attempts', '–')} | **{spoof.get('escalations', '–')}** |")
        lines += ["", "Injection detection is for the regex heuristics alone. Half of the planted payloads are",
                  "deliberately paraphrased to avoid those patterns, so this is a realistic lower bound;",
                  "`INJECTION_CLASSIFIER` adds an ML detector at ingest. Flagged chunks are wrapped as untrusted",
                  "data, and unflagged ones still sit inside the data-only context preamble.", ""]

        # --- performance
        lines += ["## 3. Performance", "", "![scale plots](scale/" + f"{scale_prefix}_scale.png)", "",
                  "| Docs | Chunks | Ingest (s) | Chunks/s | Ingest peak RSS (MB) | Store on disk (MB) | "
                  "Startup: store open + key check (s) | Startup: model load (s) | Query p50 / p95 / p99 (ms) | "
                  "Retrieval p95 (ms) |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in scale:
            i, s = r["ingest"], r["serve"]
            lat = s["latency_ms"]
            lines.append(f"| {r['corpus']['docs']:,} | {i['chunks']:,} | {i['ingest_s']} | {i['chunks_per_s']} | "
                         f"{i['peak_rss_mb']:.0f} | {r['store_size_mb']:.0f} | "
                         f"{s['startup_parts']['store_open_s']:.2f} | {s['startup_parts']['model_load_s']:.2f} | "
                         f"{lat['p50']} / {lat['p95']} / {lat['p99']} | {s['retrieval_ms']['p95']} |")
        lines.append("")

    lines += _qdrant_section({r["corpus"]["docs"]: r for r in scale})

    lines += ["## Reproduce", "", "```bash",
              "python scripts/eval_beir.py --datasets scifact fiqa",
              f"python scripts/benchmark_scale.py --docs 1000 --label {scale_prefix}_docs_1000",
              f"python scripts/benchmark_scale.py --docs 10000 --label {scale_prefix}_docs_10000",
              f"python scripts/benchmark_scale.py --docs 50000 --label {scale_prefix}_docs_50000 --timeout 7200",
              f"python scripts/plot_scale.py --prefix {scale_prefix}",
              "python scripts/evaluate.py", "```", ""]
    md = "\n".join(lines)
    payload = {"beir": beir, "scale": scale}
    (ROOT / "reports" / "evaluation.md").write_text(md, encoding="utf-8")
    (ROOT / "reports" / "evaluation.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def run_fixture_suite():
    """The original 5-document suite (smoke test), written to reports/fixture/."""
    from securerag.evaluation.runner import run_evaluation_suite

    return run_evaluation_suite(output_dir="reports/fixture", json_filename="evaluation_fixture.json",
                                md_filename="evaluation_fixture.md", iterations=15)


def run_all_evaluations():
    """Backwards-compatible entry point."""
    return build_report()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixture", action="store_true", help="also run the 5-document smoke suite")
    parser.add_argument("--scale-prefix", default="phase6")
    cli = parser.parse_args()
    if cli.fixture:
        run_fixture_suite()
    build_report(cli.scale_prefix)
    print("wrote reports/evaluation.md and reports/evaluation.json")

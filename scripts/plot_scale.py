"""Plot scale benchmark results (reports/scale/<prefix>_docs_*.json) into reports/scale/*.png.

    python scripts/plot_scale.py --prefix phase6
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(prefix: str, folder: Path) -> list[dict]:
    runs = []
    for f in folder.glob(f"{prefix}_docs_*.json"):
        r = json.loads(f.read_text(encoding="utf-8"))
        if r["ingest"]["ok"] and r.get("serve") and r["serve"].get("ok"):
            runs.append(r)
    return sorted(runs, key=lambda r: r["corpus"]["docs"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="phase6")
    parser.add_argument("--dir", default="reports/scale")
    args = parser.parse_args()

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    folder = ROOT / args.dir
    runs = load(args.prefix, folder)
    if not runs:
        print("no successful runs found", file=sys.stderr)
        return 1
    docs = [r["corpus"]["docs"] for r in runs]
    chunks = [r["ingest"]["chunks"] for r in runs]

    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    ax = axes[0][0]
    for key, label in (("p50", "p50"), ("p95", "p95"), ("p99", "p99")):
        ax.plot(chunks, [r["serve"]["latency_ms"][key] for r in runs], marker="o", label=f"end-to-end {label}")
    ax.plot(chunks, [r["serve"]["retrieval_ms"]["p95"] for r in runs], marker="s", linestyle="--",
            label="retrieval p95")
    ax.set(title="Query latency (mock LLM)", xlabel="chunks", ylabel="ms", xscale="log")
    ax.legend(fontsize=8)

    ax = axes[0][1]
    ax.plot(chunks, [r["serve"]["startup_parts"]["store_open_s"] for r in runs], marker="o",
            label="store open + key check")
    ax.plot(chunks, [r["serve"]["startup_parts"]["model_load_s"] for r in runs], marker="s",
            label="embedding model load")
    ax.set(title="API startup", xlabel="chunks", ylabel="seconds", xscale="log")
    ax.legend(fontsize=8)

    ax = axes[1][0]
    ax.plot(chunks, [r["ingest"]["chunks_per_s"] for r in runs], marker="o")
    ax.set(title="Ingest throughput (GPU embedding)", xlabel="chunks", ylabel="chunks / s", xscale="log")

    ax = axes[1][1]
    ax.plot(chunks, [r["ingest"]["peak_rss_mb"] for r in runs], marker="o", label="ingest peak RSS")
    ax.plot(chunks, [r["serve"]["peak_rss_mb"] for r in runs], marker="s", label="serve peak RSS")
    ax.plot(chunks, [r["store_size_mb"] for r in runs], marker="^", label="vector store on disk")
    ax.set(title="Memory and storage", xlabel="chunks", ylabel="MB", xscale="log")
    ax.legend(fontsize=8)

    for a in axes.flat:
        a.grid(alpha=0.3)
    fig.suptitle(f"SecureRAG scale ({', '.join(f'{d:,}' for d in docs)} docs)")
    fig.tight_layout()
    out = folder / f"{args.prefix}_scale.png"
    fig.savefig(out, dpi=120)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

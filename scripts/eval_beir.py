"""Retrieval quality on public BEIR datasets, evaluated per role under RBAC.

Each document gets a deterministic synthetic (department, clearance) label (seeded hash of its
id) and goes through the real ingestion pipeline: encryption, partitioned sparse index, and so
on. For every role, each test query is scored only against the qrels that role may see; queries
with no visible relevant document are skipped and counted. Ablations:

    dense          vector search only (RBAC pre-filtered)
    sparse         partitioned HMAC BM25 only
    hybrid         dense + sparse fused with RRF (the production path)
    hybrid_rerank  hybrid, then cross-encoder over the top 30 (authorized chunks only)

Metrics: nDCG@10, Recall@10, Recall@50, MRR@10 at document level (chunks mapped to their doc,
first occurrence wins). Every returned document is also checked against the role's policy, and
any violation is reported as a leak (must be 0).

Data: BEIR zips from https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/
(scifact.zip ~2.7 MB, fiqa.zip ~17 MB), cached under data/beir/ (git-ignored).

    python scripts/eval_beir.py --datasets scifact fiqa
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import logging
import math
import os
import re
import shutil
import ssl
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("USE_TF", "0")

from securerag.config import get_settings  # noqa: E402
from securerag.security.rbac import (  # noqa: E402
    CLEARANCE_LEVELS,
    ROLE_POLICY,
    authorize,
    build_chroma_filter,
    partitions_for_filter,
)

logger = logging.getLogger("eval_beir")

BEIR_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{name}.zip"
ROLES = ["guest", "employee", "finance_lead", "exec"]
MODES = ["dense", "sparse", "hybrid", "hybrid_rerank"]
DEPARTMENTS = sorted({d for p in ROLE_POLICY.values() for d in p["departments"]})
CLEARANCES = list(CLEARANCE_LEVELS)
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


# ------------------------------------------------------------------------------ data

def download(name: str, root: Path) -> Path:
    target = root / name
    if (target / "corpus.jsonl").exists():
        return target
    root.mkdir(parents=True, exist_ok=True)
    zpath = root / f"{name}.zip"
    url = BEIR_URL.format(name=name)
    logger.info("downloading %s", url)
    # certifi's CA bundle: some Windows Python installs lack the intermediate this server needs.
    # Verification stays on.
    import certifi

    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, context=ctx, timeout=120) as resp, zpath.open("wb") as fh:  # noqa: S310
        shutil.copyfileobj(resp, fh)
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(root)
    zpath.unlink()
    return target


def load_beir(path: Path, split: str) -> tuple[dict[str, dict], dict[str, str], dict[str, dict[str, int]]]:
    corpus = {}
    with (path / "corpus.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            corpus[str(d["_id"])] = {"title": d.get("title", ""), "text": d.get("text", "")}
    queries = {}
    with (path / "queries.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            q = json.loads(line)
            queries[str(q["_id"])] = q["text"]
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    with (path / "qrels" / f"{split}.tsv").open(encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t")
        next(reader)
        for qid, did, score in reader:
            if int(score) > 0:
                qrels[qid][did] = int(score)
    return corpus, {q: queries[q] for q in qrels if q in queries}, dict(qrels)


def rbac_label(doc_id: str, seed: int) -> tuple[str, str]:
    h = int.from_bytes(hashlib.sha256(f"{seed}:{doc_id}".encode()).digest()[:8], "big")
    return DEPARTMENTS[h % len(DEPARTMENTS)], CLEARANCES[(h // len(DEPARTMENTS)) % len(CLEARANCES)]


def materialize(corpus: dict[str, dict], out: Path, seed: int) -> dict[str, tuple[str, str]]:
    """Write one file per doc under <dept>/<clearance>/; returns doc_id -> labels."""
    marker = out / "labels.json"
    if marker.exists():
        meta = json.loads(marker.read_text(encoding="utf-8"))
        if meta["seed"] == seed and meta["docs"] == len(corpus):
            return {k: tuple(v) for k, v in meta["labels"].items()}
    labels = {}
    for doc_id, doc in corpus.items():
        dept, clr = rbac_label(doc_id, seed)
        labels[doc_id] = (dept, clr)
        f = out / dept / clr / f"{_SAFE.sub('_', doc_id)}__{hashlib.md5(doc_id.encode()).hexdigest()[:6]}.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"{doc['title']}\n\n{doc['text']}".strip() + "\n", encoding="utf-8")
    marker.write_text(json.dumps({"seed": seed, "docs": len(corpus), "labels": labels}), encoding="utf-8")
    return labels


# ------------------------------------------------------------------------------ metrics

def ndcg_at(ranked: list[str], rel: dict[str, int], k: int = 10) -> float:
    dcg = sum((2 ** rel.get(d, 0) - 1) / math.log2(i + 2) for i, d in enumerate(ranked[:k]))
    ideal = sorted(rel.values(), reverse=True)[:k]
    idcg = sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def recall_at(ranked: list[str], rel: dict[str, int], k: int) -> float:
    return len(set(ranked[:k]) & set(rel)) / len(rel) if rel else 0.0


def mrr_at(ranked: list[str], rel: dict[str, int], k: int = 10) -> float:
    for i, d in enumerate(ranked[:k]):
        if d in rel:
            return 1.0 / (i + 1)
    return 0.0


# ------------------------------------------------------------------------------ evaluation

class Evaluator:
    def __init__(self, stack: Any, encryptor: Any, reranker: Any, path_to_doc: dict[str, str]):
        self.stack = stack
        self.encryptor = encryptor
        self.reranker = reranker
        self.path_to_doc = path_to_doc
        self._meta_cache: dict[str, dict] = {}

    def _docs(self, chunk_ids: list[str]) -> list[str]:
        missing = [c for c in chunk_ids if c not in self._meta_cache]
        for s in range(0, len(missing), 500):
            self._meta_cache.update(self.stack.store.get_metadata(missing[s:s + 500]))
        seen, out = set(), []
        for c in chunk_ids:
            meta = self._meta_cache.get(c)
            if meta is None:
                continue
            doc = self.path_to_doc[meta["path"]]
            if doc not in seen:
                seen.add(doc)
                out.append(doc)
        return out

    def rank(self, mode: str, query: str, q_emb: list[float], where: dict) -> list[str]:
        retriever = self.stack.retriever
        if mode == "dense":
            return self._docs([c for c, _ in self.stack.store.query(q_emb, 100, where)])
        if mode == "sparse":
            return self._docs([c for c, _ in self.stack.sparse.search(query, partitions_for_filter(where), 100)])
        fused = [c for c, _ in retriever.candidates(query, where)]
        if mode == "hybrid":
            return self._docs(fused)
        head, tail = fused[:30], fused[30:]
        rows = self.stack.store.get(head)
        from securerag.security.encryption import aad_for

        texts = [self.encryptor.decrypt(ct, aad=aad_for(cid, meta)) for cid, ct, meta in rows]
        scores = self.reranker.score(query, texts)
        reranked = [cid for (cid, _, _), _ in sorted(zip(rows, scores, strict=True), key=lambda x: -x[1])]
        return self._docs(reranked + tail)


def evaluate_dataset(name: str, args: argparse.Namespace) -> dict[str, Any]:
    from securerag.retrieval.rerank import CrossEncoderReranker
    from securerag.retrieval.store import open_serving_stack, run_ingestion
    from securerag.security.encryption import VectorStoreEncryptor

    base = ROOT / "data" / "beir"
    raw = download(name, base)
    corpus, queries, qrels = load_beir(raw, args.split)
    if args.max_queries:
        keep = sorted(queries)[: args.max_queries]
        queries = {q: queries[q] for q in keep}
    work = base / f"{name}_rbac"
    labels = materialize(corpus, work / "corpus", args.seed)

    key_file = work / "aes_key.b64"
    if not key_file.exists():
        key_file.write_text(base64.b64encode(os.urandom(32)).decode(), encoding="ascii")
    encryptor = VectorStoreEncryptor(key_b64=key_file.read_text(encoding="ascii"))
    settings = get_settings().model_copy(update={
        "data_dir": str(work / "corpus"), "chroma_dir": str(work / "chroma"), "state_db_path": "", "sparse_dir": ""})

    t0 = time.perf_counter()
    report = run_ingestion(settings, encryptor)
    ingest_s = time.perf_counter() - t0
    logger.info("%s ingest: %s", name, {k: report.as_dict()[k] for k in ("indexed_new", "unchanged",
                                                                          "chunks_written", "elapsed_s")})
    stack = open_serving_stack(settings, encryptor)
    reranker = CrossEncoderReranker(args.rerank_model, settings.embed_device) if "hybrid_rerank" in args.modes else None
    # Map stored chunk paths back to BEIR doc ids through the file naming scheme used by materialize().
    by_name = {f"{_SAFE.sub('_', d)}__{hashlib.md5(d.encode()).hexdigest()[:6]}": d for d in corpus}
    path_to_doc = {p.relative_to(work / "corpus").as_posix(): by_name[p.stem] for p in (work / "corpus").rglob("*.txt")}
    ev = Evaluator(stack, encryptor, reranker, path_to_doc)

    qids = sorted(queries)
    q_embs = stack.encoder.encode([settings.embed_query_prefix + queries[q] for q in qids],
                                  normalize_embeddings=True, batch_size=64)
    results: dict[str, Any] = {}
    for role in ROLES:
        where = build_chroma_filter(role)
        role_res: dict[str, Any] = {}
        evaluable = [q for q in qids if any(authorize(role, dict(zip(("department", "clearance"), labels[d],
                                                                     strict=True)))
                                          for d in qrels[q] if d in labels)]
        for mode in args.modes:
            sums = defaultdict(float)
            leaks = 0
            t_mode = time.perf_counter()
            for q in evaluable:
                visible = {d: r for d, r in qrels[q].items() if d in labels and authorize(
                    role, {"department": labels[d][0], "clearance": labels[d][1]})}
                ranked = ev.rank(mode, queries[q], q_embs[qids.index(q)].tolist(), where)
                leaks += sum(1 for d in ranked if not authorize(role, {"department": labels[d][0],
                                                                        "clearance": labels[d][1]}))
                sums["ndcg@10"] += ndcg_at(ranked, visible)
                sums["recall@10"] += recall_at(ranked, visible, 10)
                sums["recall@50"] += recall_at(ranked, visible, 50)
                sums["mrr@10"] += mrr_at(ranked, visible)
            n = max(1, len(evaluable))
            role_res[mode] = {k: round(v / n, 4) for k, v in sums.items()} | {
                "queries": len(evaluable), "leaks": leaks,
                "ms_per_query": round((time.perf_counter() - t_mode) * 1000 / n, 2)}
            logger.info("%s %s %s: %s", name, role, mode, role_res[mode])
        results[role] = role_res
    return {"dataset": name, "split": args.split, "docs": len(corpus), "test_queries": len(queries),
            "chunks": stack.store.count(), "ingest_s": round(ingest_s, 1), "embed_model": settings.embed_model,
            "rerank_model": args.rerank_model, "seed": args.seed, "results": results}


def render(result: dict[str, Any]) -> str:
    lines = [f"# BEIR {result['dataset']} ({result['split']}) under RBAC", "",
             f"- Docs: {result['docs']:,} -> {result['chunks']:,} encrypted chunks (ingest {result['ingest_s']} s)",
             f"- Test queries: {result['test_queries']}; embedder `{result['embed_model']}`; "
             f"reranker `{result['rerank_model']}`; label seed {result['seed']}",
             "- Per role, only qrels the role may see count; queries with none visible are skipped.", ""]
    for role, modes in result["results"].items():
        first = next(iter(modes.values()))
        lines += [f"## {role} ({first['queries']} evaluable queries)", "",
                  "| Mode | nDCG@10 | Recall@10 | Recall@50 | MRR@10 | ms/query | Leaks |",
                  "|---|---|---|---|---|---|---|"]
        for mode, m in modes.items():
            lines.append(f"| {mode} | {m['ndcg@10']:.4f} | {m['recall@10']:.4f} | {m['recall@50']:.4f} | "
                         f"{m['mrr@10']:.4f} | {m['ms_per_query']} | {m['leaks']} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--datasets", nargs="+", default=["scifact", "fiqa"])
    parser.add_argument("--split", default="test")
    parser.add_argument("--modes", nargs="+", default=MODES, choices=MODES)
    parser.add_argument("--rerank-model", default="cross-encoder/ms-marco-MiniLM-L-6-v2")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-queries", type=int, default=0)
    parser.add_argument("--out", default="reports/beir")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.environ.setdefault("LLM_PROVIDER", "mock")

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    for name in args.datasets:
        result = evaluate_dataset(name, args)
        (out / f"{name}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (out / f"{name}.md").write_text(render(result), encoding="utf-8")
        logger.info("wrote %s", out / f"{name}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())

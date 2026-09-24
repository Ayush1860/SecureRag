# Scale baseline (pre-change pipeline, commit `e79094b`)

Generated with `scripts/benchmark_scale.py` on the unmodified pipeline: 8 CPUs, 15.8 GB RAM,
NVIDIA GTX 1650 (embeddings on CUDA), Windows 11, Python 3.11.4. Synthetic corpus from
`scripts/generate_corpus.py --seed 42 --avg-words 400`. LLM is the deterministic mock.
Per-run details: [docs_500](docs_500.md), [docs_1000](docs_1000.md), [docs_2000](docs_2000.md).

| Metric | 500 docs | 1000 docs | 2000 docs |
|---|---|---|---|
| Chunks (800-char fixed split) | 2,344 | 4,685 | ~9,412 (never stored) |
| Ingest status | OK | OK | **FAILED** |
| Ingest wall time (s) | 24.7 | 38.3 | 45.0 until crash |
| Chunks/sec | 95 | 122 | – |
| Ingest peak RSS (MB) | 1,083 | 1,177 | 1,213 |
| Store on disk (MB) | 39.0 | 75.1 | – |
| Engine startup (s) | 13.7 | 13.9 | – |
| Serve peak RSS (MB) | 1,024 | 1,056 | – |
| Query p50 / p95 / p99 (ms) | 41.0 / 52.2 / 57.4 | 47.7 / 63.1 / 66.8 | – |
| Retrieval p50 / p95 (ms) | 37.0 / 48.3 | 43.9 / 59.3 | – |
| Canary leaks (800 queries) | 0 | 0 | – |

## Findings

1. **2000 docs cannot be ingested.** `build_store` calls `collection.add` once with every chunk:
   `chromadb.errors.InternalError: ValueError: Batch size of 9412 is greater than max batch size of 5461`.
   The failure comes after all embeddings are computed, so ~45 s of work is lost, and
   `delete_collection` has already wiped the previous store. The ceiling is ~5.4k chunks (~1,150 docs).
2. **Retrieval latency grows linearly with corpus size.** Retrieval p50 went from 37 ms to 44 ms when
   the corpus doubled, driven by `BM25Okapi.get_scores` over the whole corpus plus the Python RBAC loop.
   Roles that see more partitions are slower (guest 20 ms, exec 42 ms at 500 docs) because the sparse
   candidate list is built by iterating all chunks the role may see.
3. **Startup includes decrypting the whole corpus.** Startup is dominated by the ~10 s model load at this
   size, but `load_store` decrypts every chunk and holds all plaintext in RAM (serve RSS grows with corpus).
4. **No leaks at this size.** RBAC pre-filter + post-filter held for 800 adversarial/topical queries per corpus.

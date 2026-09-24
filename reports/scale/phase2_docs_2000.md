# Scale benchmark: phase2_docs_2000

- Generated: 2026-09-25 03:47:59
- Git commit: `2fd2960`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_2000` (2000 docs, 834,308 words, 665 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 6233 |
| Ingest wall time (s) | 60.41 |
| Chunks/sec | 103.2 |
| Process wall time incl. imports (s) | 61.35 |
| Peak RSS (MB) | 1085.7 |
| Vector store on disk (MB) | 99.3 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 13.42 |
| - embedding model load (s) | 9.76 |
| - store open + key check + retriever (s) | 1.099 |
| Serve process peak RSS (MB) | 1047.0 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 24.17 | 25.42 | 26.7 | 18.95 | 20.14 | 0 | 0 |
| employee | 200 | 0 | 38.73 | 41.06 | 43.02 | 33.38 | 35.35 | 0 | 0 |
| finance_lead | 200 | 0 | 39.18 | 42.47 | 47.31 | 33.84 | 36.94 | 0 | 591 |
| exec | 200 | 0 | 26.3 | 28.5 | 29.64 | 21.08 | 22.83 | 0 | 570 |
| **all** | | | 37.33 | 40.79 | 43.68 | 32.16 | 35.28 | 0 | |

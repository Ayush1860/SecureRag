# Scale benchmark: phase4_docs_2000

- Generated: 2026-09-25 04:14:49
- Git commit: `56c0580`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_2000` (2000 docs, 834,308 words, 665 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 6233 |
| Ingest wall time (s) | 61.11 |
| Chunks/sec | 102.0 |
| Process wall time incl. imports (s) | 62.05 |
| Peak RSS (MB) | 1087.5 |
| Vector store on disk (MB) | 100.16 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 13.17 |
| - embedding model load (s) | 9.748 |
| - store open + key check + retriever (s) | 1.155 |
| Serve process peak RSS (MB) | 1043.4 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 31.68 | 33.27 | 33.65 | 19.07 | 19.99 | 0 | 0 |
| employee | 200 | 0 | 45.86 | 47.88 | 49.95 | 33.12 | 34.64 | 0 | 0 |
| finance_lead | 200 | 0 | 45.71 | 48.63 | 57.56 | 33.14 | 35.18 | 0 | 591 |
| exec | 200 | 0 | 32.82 | 36.6 | 41.37 | 20.32 | 23.54 | 0 | 571 |
| **all** | | | 43.88 | 47.44 | 51.08 | 31.83 | 34.35 | 0 | |

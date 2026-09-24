# Scale benchmark: phase2_docs_1000

- Generated: 2026-09-25 03:43:01
- Git commit: `2fd2960`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_1000` (1000 docs, 414,740 words, 302 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 3101 |
| Ingest wall time (s) | 36.44 |
| Chunks/sec | 85.1 |
| Process wall time incl. imports (s) | 37.33 |
| Peak RSS (MB) | 1075.0 |
| Vector store on disk (MB) | 52.73 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 13.47 |
| Serve process peak RSS (MB) | 1036.0 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 21.68 | 23.39 | 24.03 | 16.52 | 18.02 | 0 | 0 |
| employee | 200 | 0 | 26.86 | 29.51 | 30.86 | 21.72 | 24.25 | 0 | 0 |
| finance_lead | 200 | 0 | 26.44 | 29.65 | 34.85 | 21.31 | 24.25 | 0 | 537 |
| exec | 200 | 0 | 24.28 | 26.45 | 27.85 | 19.18 | 21.21 | 0 | 556 |
| **all** | | | 25.55 | 28.72 | 33.22 | 20.44 | 23.32 | 0 | |

# Scale benchmark: docs_500

- Generated: 2026-09-25 02:36:59
- Git commit: `e79094b`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_500` (500 docs, 207,503 words, 143 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 2344 |
| Ingest wall time (s) | 24.65 |
| Chunks/sec | 95.1 |
| Process wall time incl. imports (s) | 25.58 |
| Peak RSS (MB) | 1082.7 |
| Vector store on disk (MB) | 39.03 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 13.73 |
| Serve process peak RSS (MB) | 1024.3 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 24.06 | 33.36 | 39.81 | 19.87 | 27.68 | 0 | 0 |
| employee | 200 | 0 | 40.39 | 48.62 | 52.23 | 36.32 | 44.05 | 0 | 0 |
| finance_lead | 200 | 0 | 41.91 | 52.42 | 63.32 | 37.73 | 48.51 | 0 | 708 |
| exec | 200 | 0 | 46.06 | 54.19 | 57.42 | 42.43 | 50.39 | 0 | 685 |
| **all** | | | 40.97 | 52.2 | 57.35 | 36.97 | 48.32 | 0 | |

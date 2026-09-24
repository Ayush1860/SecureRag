# Scale benchmark: phase1_docs_500

- Generated: 2026-09-25 02:46:53
- Git commit: `ae7a737`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_500` (500 docs, 207,503 words, 143 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 1546 |
| Ingest wall time (s) | 24.11 |
| Chunks/sec | 64.1 |
| Process wall time incl. imports (s) | 24.97 |
| Peak RSS (MB) | 1036.3 |
| Vector store on disk (MB) | 26.03 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 13.84 |
| Serve process peak RSS (MB) | 1022.6 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 22.24 | 26.17 | 28.49 | 17.62 | 21.0 | 0 | 0 |
| employee | 200 | 0 | 26.76 | 29.66 | 32.18 | 22.52 | 25.17 | 0 | 0 |
| finance_lead | 200 | 0 | 27.29 | 30.95 | 32.39 | 23.14 | 26.29 | 0 | 708 |
| exec | 200 | 0 | 30.93 | 48.51 | 62.13 | 26.92 | 41.99 | 0 | 721 |
| **all** | | | 27.14 | 35.11 | 49.11 | 22.95 | 30.44 | 0 | |

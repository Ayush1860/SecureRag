# Scale benchmark: phase1_docs_1000

- Generated: 2026-09-25 02:48:23
- Git commit: `ae7a737`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_1000` (1000 docs, 414,740 words, 302 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 3101 |
| Ingest wall time (s) | 35.95 |
| Chunks/sec | 86.3 |
| Process wall time incl. imports (s) | 36.84 |
| Peak RSS (MB) | 1050.5 |
| Vector store on disk (MB) | 51.96 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 13.69 |
| Serve process peak RSS (MB) | 1050.7 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 32.15 | 37.23 | 38.24 | 27.8 | 32.87 | 0 | 0 |
| employee | 200 | 0 | 40.18 | 46.32 | 51.38 | 35.93 | 41.9 | 0 | 0 |
| finance_lead | 200 | 0 | 40.55 | 46.22 | 48.0 | 36.65 | 41.81 | 0 | 699 |
| exec | 200 | 0 | 51.47 | 61.48 | 78.72 | 47.34 | 56.1 | 0 | 745 |
| **all** | | | 40.4 | 55.04 | 65.58 | 36.25 | 50.79 | 0 | |

# Scale benchmark: phase1_docs_2000

- Generated: 2026-09-25 02:50:43
- Git commit: `ae7a737`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_2000` (2000 docs, 834,308 words, 665 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 6233 |
| Ingest wall time (s) | 60.16 |
| Chunks/sec | 103.6 |
| Process wall time incl. imports (s) | 61.05 |
| Peak RSS (MB) | 1065.7 |
| Vector store on disk (MB) | 98.7 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 14.4 |
| Serve process peak RSS (MB) | 1112.3 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 49.96 | 61.37 | 63.2 | 45.8 | 57.07 | 0 | 0 |
| employee | 200 | 0 | 67.87 | 80.27 | 84.86 | 63.73 | 75.85 | 0 | 0 |
| finance_lead | 200 | 0 | 71.54 | 83.87 | 85.37 | 67.5 | 79.59 | 0 | 733 |
| exec | 200 | 0 | 93.88 | 108.07 | 130.43 | 89.82 | 103.5 | 0 | 738 |
| **all** | | | 70.3 | 102.89 | 111.63 | 66.35 | 98.68 | 0 | |

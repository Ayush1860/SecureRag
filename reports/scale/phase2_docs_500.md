# Scale benchmark: phase2_docs_500

- Generated: 2026-09-25 03:46:12
- Git commit: `2fd2960`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_500` (500 docs, 207,503 words, 143 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 1546 |
| Ingest wall time (s) | 25.12 |
| Chunks/sec | 61.5 |
| Process wall time incl. imports (s) | 26.05 |
| Peak RSS (MB) | 1063.2 |
| Vector store on disk (MB) | 26.12 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 13.33 |
| - embedding model load (s) | 9.61 |
| - store open + key check + retriever (s) | 1.102 |
| Serve process peak RSS (MB) | 1027.9 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 21.16 | 25.13 | 28.99 | 15.92 | 19.07 | 0 | 0 |
| employee | 200 | 0 | 24.02 | 26.06 | 27.08 | 18.85 | 20.48 | 0 | 0 |
| finance_lead | 200 | 0 | 24.08 | 25.63 | 26.77 | 18.87 | 20.11 | 0 | 507 |
| exec | 200 | 0 | 23.77 | 25.6 | 27.34 | 18.79 | 20.66 | 0 | 551 |
| **all** | | | 23.73 | 25.77 | 27.96 | 18.62 | 20.32 | 0 | |

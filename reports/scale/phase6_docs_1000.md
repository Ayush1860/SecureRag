# Scale benchmark: phase6_docs_1000

- Generated: 2026-09-25 05:38:55
- Git commit: `670cbaf`
- Hardware: 8 CPUs, 15.8 GB RAM, NVIDIA GeForce GTX 1650, Python 3.11.4, Windows-10-10.0.26200-SP0
- Corpus: `data/synthetic/docs_1000` (1000 docs, 414,765 words, 302 canaries)

## Ingest

| Metric | Value |
|---|---|
| Status | OK |
| Chunks | 3101 |
| Ingest wall time (s) | 36.92 |
| Chunks/sec | 84.0 |
| Process wall time incl. imports (s) | 37.81 |
| Peak RSS (MB) | 1074.3 |
| Vector store on disk (MB) | 52.88 |

## Serving

| Metric | Value |
|---|---|
| Status | OK |
| Engine startup (s) | 12.8 |
| - embedding model load (s) | 9.369 |
| - store open + key check + retriever (s) | 1.14 |
| Serve process peak RSS (MB) | 1068.5 |
| **Canary leaks** | **0** |

| Role | Queries | Errors | p50 ms | p95 ms | p99 ms | Retrieval p50 | Retrieval p95 | Leaks | Own canaries seen |
|---|---|---|---|---|---|---|---|---|---|
| guest | 200 | 0 | 29.94 | 32.32 | 33.68 | 16.96 | 18.6 | 0 | 0 |
| employee | 200 | 0 | 31.89 | 37.77 | 41.08 | 18.74 | 25.08 | 0 | 0 |
| finance_lead | 200 | 0 | 31.82 | 34.47 | 39.62 | 18.91 | 20.93 | 0 | 535 |
| exec | 200 | 0 | 32.31 | 34.46 | 35.94 | 19.53 | 21.37 | 0 | 557 |
| **all** | | | 31.7 | 34.53 | 39.46 | 18.81 | 21.06 | 0 | |

## Ingest-time injection detection (per chunk)

| Chunks | TP | FP | FN | Detection rate | FPR |
|---|---|---|---|---|---|
| 3101 | 14 | 0 | 10 | 0.5833 | 0.0 |

## Role-spoofing attempts (AUTH_MODE=api_key)

25 attempts, **0 escalations**, block rate 100.00%.
